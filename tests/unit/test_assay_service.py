import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from config import settings
from utils.osu import assay_service, pp_calculator

@pytest_asyncio.fixture
async def service(monkeypatch):
    asked = []

    async def score(request):
        body = await request.json()
        asked.append(("score", body, request.headers.get("Authorization")))
        return web.json_response({
            "pp": 301.234, "pp_if_fc": 350.0, "pp_if_ss": 400.0, "star_rating": 6.789,
            "accuracy": 0.97, "max_combo": 900,
            "map": {"max_combo": 1000, "star_rating": 6.789},
        })

    async def whatif(request):
        body = await request.json()
        asked.append(("whatif", body, None))
        points = [{"accuracy": a / 100, "pp": 100 + a,
                   "statistics": {"great": 900, "ok": 10, "meh": 1}} for a in body["accuracies"]]
        return web.json_response({"points": points, "map": {"max_combo": 1000, "star_rating": 6.5}})

    app = web.Application()
    app.router.add_post("/v1/score", score)
    app.router.add_post("/v1/whatif", whatif)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(settings, "ASSAY_URL", str(server.make_url("")).rstrip("/"))
    monkeypatch.setattr(settings, "ASSAY_TOKEN", "t0ken")
    yield asked
    await assay_service.close()
    await server.close()

def test_mods_keep_their_settings_and_drop_nm():
    assert assay_service.mods_of("HDDT") == [{"acronym": "HD"}, {"acronym": "DT"}]
    assert assay_service.mods_of("NM") == []
    assert assay_service.mods_of([{"acronym": "dt", "settings": {"speed_change": 1.2}}, "HD"]) == [
        {"acronym": "DT", "settings": {"speed_change": 1.2}}, {"acronym": "HD"},
    ]

async def test_a_score_is_counted_by_the_service_with_everything_it_needs(service):
    stats = {"great": 900, "ok": 10, "miss": 2, "slider_tail_hit": 300}
    got = await pp_calculator.calculate_pp(
        beatmap_id=77, mods_str="HDDT", accuracy=97.0, combo=800, misses=2,
        statistics=stats, mods=[{"acronym": "DT", "settings": {"speed_change": 1.2}}],
        checksum="a" * 32, legacy_total_score=None, is_legacy=False,
    )
    assert got == {"pp_current": 301.23, "pp_if_fc": 350.0, "pp_if_ss": 400.0,
                   "star_rating": 6.79, "max_combo": 1000, "source": "assay"}
    kind, body, auth = service[0]
    assert kind == "score" and auth == "Bearer t0ken"
    assert body["statistics"] == stats
    assert body["mods"] == [{"acronym": "DT", "settings": {"speed_change": 1.2}}]
    assert body["checksum"] == "a" * 32
    assert body["accuracy"] == pytest.approx(0.97)
    assert body["max_combo"] == 800

async def test_without_full_statistics_the_basic_counts_are_sent(service):
    await pp_calculator.calculate_pp(beatmap_id=77, mods_str="HD", accuracy=99.0,
                                     count_300=500, count_100=3, count_50=0, misses=1)
    body = service[0][1]
    assert body["statistics"] == {"great": 500, "ok": 3, "meh": 0, "miss": 1}
    assert body["mods"] == [{"acronym": "HD"}]

async def test_whatif_comes_from_the_service_with_its_brackets(service):
    got = await pp_calculator.calculate_whatif_pp(77, 97.5, "HD", "b" * 32)
    assert got["pp"] == 197.5
    assert got["brackets"] == {95.0: 195.0, 98.0: 198.0, 99.0: 199.0, 100.0: 200.0}
    assert got["star_rating"] == 6.5 and got["combo"] == 1000
    assert (got["count_300"], got["count_100"], got["count_50"], got["count_miss"]) == (900, 10, 1, 0)
    assert service[0][1]["accuracies"] == [95.0, 98.0, 99.0, 100.0, 97.5]

async def test_a_service_that_is_down_leaves_the_old_way(monkeypatch):
    monkeypatch.setattr(settings, "ASSAY_URL", "http://127.0.0.1:9")

    async def engine(*_a, **_k):
        return {"pp": 5.0, "pp_if_unbroken": 6.0, "pp_if_perfect": 7.0, "star_rating": 3.0, "max_combo": 10}

    monkeypatch.setattr(pp_calculator.assay, "for_score", engine)
    got = await pp_calculator.calculate_pp(beatmap_id=77, mods_str="", accuracy=90.0,
                                           statistics={"great": 1})
    await assay_service.close()
    assert got["source"] == "engine" and got["pp_current"] == 5.0

async def test_nothing_is_asked_when_the_service_is_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "ASSAY_URL", "")
    assert await assay_service.score(77, mods="HD", statistics={"great": 1}) is None
    assert await assay_service.whatif(77, [99.0], mods="") is None

def test_a_drift_is_reported_past_one_percent(monkeypatch):
    warned = []
    monkeypatch.setattr(pp_calculator.logger, "warning", lambda *a, **_k: warned.append(a))
    assert pp_calculator.note_drift(1, 77, 100.0, {"pp_current": 100.5, "source": "assay"}) == pytest.approx(0.005)
    assert not warned
    assert pp_calculator.note_drift(1, 77, 100.0, {"pp_current": 103.0, "source": "assay"}) == pytest.approx(0.03)
    assert len(warned) == 1
    assert pp_calculator.note_drift(1, 77, 0, {"pp_current": 3.0}) is None
