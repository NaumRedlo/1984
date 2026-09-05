import json
import os
import sys
import time

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bot.handlers.dossier import renders
from services.miniapp import api
from tests.unit.test_miniapp_auth import TOKEN, signed

@pytest_asyncio.fixture
async def app(monkeypatch):
    held = {"choices": renders.Choices()}

    async def load(_tg, _tenant):
        return held["choices"]

    async def store(_tg, _tenant, choices):
        held["choices"] = choices

    async def tenant(_tg):
        return -1001

    async def no_refusal(*_a, **_kw):
        return None

    async def language(_tg):
        return "ru"

    monkeypatch.setattr(api, "MINIAPP_ENABLED", True)
    monkeypatch.setattr(api, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(api, "_load", load)
    monkeypatch.setattr(api, "_store", store)
    monkeypatch.setattr(api, "_tenant", tenant)
    monkeypatch.setattr(api, "rationed", no_refusal)

    async def five_left(_tg, _tenant):
        return 5

    monkeypatch.setattr(api, "_ration", five_left)
    monkeypatch.setattr(api, "get_language", language)
    monkeypatch.setattr(api, "can_use_render", lambda _tg: True)

    application = web.Application()
    assert api.install(application)
    client = TestClient(TestServer(application))
    await client.start_server()
    yield client, held
    await client.close()

def auth(blob: str | None = None) -> dict:
    return {"Authorization": f"tma {blob if blob is not None else signed()}"}

async def test_a_request_with_no_signature_is_refused(app):
    client, _ = app
    assert (await client.get("/app/api/settings")).status == 401

async def test_a_request_signed_by_another_bot_is_refused(app):
    client, _ = app
    reply = await client.get("/app/api/settings", headers=auth(signed("999:other")))
    assert reply.status == 401

async def test_a_refusal_does_not_say_which_check_failed(app):
    client, _ = app
    stale = signed(at=time.time() - 10 * 24 * 3600)
    wrong = signed("999:other")
    said = set()
    for blob in (stale, wrong):
        reply = await client.get("/app/api/settings", headers=auth(blob))
        said.add(await reply.text())
    assert len(said) == 1, f"the two refusals differ: {said}"

async def test_somebody_without_render_access_is_told_so(app, monkeypatch):
    client, _ = app
    monkeypatch.setattr(api, "can_use_render", lambda _tg: False)
    assert (await client.get("/app/api/settings", headers=auth())).status == 403

async def test_reading_gives_the_settings_and_how_to_draw_them(app):
    client, _ = app
    body = await (await client.get("/app/api/settings", headers=auth())).json()

    assert body["settings"]["fps"] == 60
    assert body["heavy_left"] == 5, "the page cannot draw the 4K control without it"

    assert set(body["fields"]) == set(body["settings"])
    assert body["fields"]["mute"]["kind"] == "switch"
    assert body["fields"]["meter"] == {
        "kind": "number", "label": "Шкала точности",
        "hint": "От 50 до 300 процентов.", "low": 50, "high": 300,
    }
    assert body["fields"]["size"]["kind"] == "text", "a size is not a slider"

    grouped = {k for group in body["groups"] for k in group["keys"]}
    assert grouped == set(body["settings"]), "a setting belongs to no group"
    assert body["groups"][0]["label"] == "Рендер"

async def test_a_change_is_stored(app):
    client, held = app
    reply = await client.post(
        "/app/api/settings", headers=auth(), json={"fps": 30, "mute": True}
    )
    assert reply.status == 200
    assert held["choices"].fps == 30 and held["choices"].mute is True
    assert (await reply.json())["settings"]["fps"] == 30

async def test_a_key_nobody_knows_is_named_and_refused(app):
    client, held = app
    reply = await client.post(
        "/app/api/settings", headers=auth(), json={"fps": 30, "colour": "red"}
    )
    assert reply.status == 400
    assert (await reply.json())["keys"] == ["colour"]
    assert held["choices"].fps == 60, "a refused write changed something anyway"

async def test_a_field_the_engine_needs_is_not_web_writable(app):
    client, _ = app
    reply = await client.post(
        "/app/api/settings", headers=auth(), json={"skin": "/etc/passwd"}
    )
    assert reply.status == 400

async def test_what_a_person_is_shown_is_in_their_own_language(app):
    client, _ = app
    reply = await client.post("/app/api/settings", headers=auth(), json={"meter": 30})
    said = await reply.json()

    assert said["code"] == "out-of-range", "the page still needs a code to act on"
    assert said["error"] == "Такое значение не подходит. От 50 до 300 процентов."
    assert said["key"] == "meter", "and which row to point at"

async def test_a_value_out_of_range_is_refused_by_the_bots_own_parser(app):
    client, held = app
    for key, bad in (("fps", 9000), ("meter", 30), ("size", "1601x900")):
        reply = await client.post("/app/api/settings", headers=auth(), json={key: bad})
        assert reply.status == 400, f"{key}={bad} was accepted"
        assert (await reply.json())["key"] == key
    assert held["choices"] == renders.Choices(), "a refused write leaked through"

async def test_a_switch_wants_a_yes_or_no(app):
    client, _ = app
    reply = await client.post("/app/api/settings", headers=auth(), json={"mute": "yes"})
    assert reply.status == 400

async def test_as_it_comes_is_a_real_choice(app):
    client, held = app
    held["choices"].dim = 80
    reply = await client.post("/app/api/settings", headers=auth(), json={"dim": None})
    assert reply.status == 200
    assert held["choices"].dim is None

async def test_the_ration_is_asked_before_a_big_size_is_stored(app, monkeypatch):
    asked = []

    async def refuse(tg, tenant, before, after, lang):
        asked.append(after.size)
        return "нет квоты на сегодня"

    monkeypatch.setattr(api, "rationed", refuse)
    client, held = app
    reply = await client.post(
        "/app/api/settings", headers=auth(), json={"size": "3840x2160"}
    )
    assert reply.status == 409
    assert asked == ["3840x2160"]
    assert held["choices"].size == "1280x720", "the ration was asked and ignored"

async def test_an_empty_body_changes_nothing(app):
    client, _ = app
    assert (await client.post("/app/api/settings", headers=auth(), json={})).status == 400

async def test_a_body_that_is_not_an_object_is_refused(app):
    client, _ = app
    reply = await client.post(
        "/app/api/settings",
        headers={**auth(), "Content-Type": "application/json"},
        data=json.dumps(["fps", 30]),
    )
    assert reply.status == 400

@pytest_asyncio.fixture
async def store(monkeypatch, tmp_path):
    from services.dossier import preview
    from dossier import skins as skin_store

    folders = {"mine-one": True, "theirs-one": True, "no-circle": False}
    for name in folders:
        (tmp_path / name).mkdir()

    monkeypatch.setattr(api.skin_store, "by_owner",
                        lambda _tg: (["mine-one"], ["theirs-one", "no-circle"]))
    monkeypatch.setattr(api.skin_store, "stale", lambda: ["theirs-one"])
    monkeypatch.setattr(
        api.skin_store, "folder_of",
        lambda name: str(tmp_path / name) if name in folders else None,
    )

    monkeypatch.setattr(
        api.preview, "path_of",
        lambda name, **_kw: str(tmp_path / f"{name}.png") if folders.get(name) else None,
    )
    for name, has in folders.items():
        if has:
            (tmp_path / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    del preview, skin_store
    yield tmp_path

async def test_the_skins_come_split_the_way_the_bots_own_picker_splits_them(app, store):
    client, _ = app
    said = await (await client.get("/app/api/skins", headers=auth())).json()

    assert [s["name"] for s in said["mine"]] == ["mine-one"]

    assert [s["name"] for s in said["shared"]] == ["classic", "theirs-one", "no-circle"]
    assert said["current"] == "classic"

async def test_a_skin_with_nothing_to_show_says_so_rather_than_borrowing_a_picture(
    app, store
):
    client, _ = app
    said = await (await client.get("/app/api/skins", headers=auth())).json()
    by_name = {s["name"]: s for s in said["shared"]}

    assert by_name["theirs-one"]["preview"] == "/app/preview/theirs-one.png"
    assert by_name["no-circle"]["preview"] is None

async def test_a_skin_unpacked_by_older_code_is_marked(app, store):
    client, _ = app
    said = await (await client.get("/app/api/skins", headers=auth())).json()
    by_name = {s["name"]: s for s in said["shared"]}
    assert by_name["theirs-one"]["stale"] and not by_name["no-circle"]["stale"]

async def test_choosing_a_skin_stores_it(app, store):
    client, held = app
    reply = await client.post("/app/api/skin", headers=auth(), json={"name": "mine-one"})
    assert reply.status == 200
    assert held["choices"].skin == "mine-one"

async def test_choosing_the_engines_own_look_stores_no_skin_at_all(app, store):
    client, held = app
    held["choices"].skin = "mine-one"
    await client.post("/app/api/skin", headers=auth(), json={"name": "classic"})
    assert held["choices"].skin is None

async def test_a_skin_that_is_not_in_the_store_is_refused(app, store):
    client, held = app
    for tried in ("gone", "../../etc/passwd", "", None):
        reply = await client.post("/app/api/skin", headers=auth(), json={"name": tried})
        assert reply.status == 404, tried
    assert held["choices"].skin is None

async def test_choosing_a_skin_needs_a_signature(app, store):
    client, held = app
    reply = await client.post("/app/api/skin", json={"name": "mine-one"})
    assert reply.status == 401
    assert held["choices"].skin is None

async def test_a_preview_is_served_without_a_signature(app, store):
    client, _ = app
    reply = await client.get("/app/preview/mine-one.png")
    assert reply.status == 200
    assert reply.headers["Content-Type"] == "image/png"

async def test_a_name_that_is_not_a_skin_reaches_no_file(app, store):
    client, _ = app
    for tried in ("../../etc/passwd", "no-circle", "nothing"):
        assert (await client.get(f"/app/preview/{tried}.png")).status == 404, tried

async def test_the_farm_says_why_in_the_readers_language(app, monkeypatch):
    from services.render_farm.roster import Roster

    roster = Roster()
    roster.hello("laptop", build=None, capacity={
        "take": False, "reason": "on battery at 12%",
        "code": "battery", "detail": "12", "threads": 0,
    })
    monkeypatch.setattr(api, "render_roster", roster)

    client, _ = app
    said = await (await client.get("/app/api/farm", headers=auth())).json()
    assert said["workers"][0]["reason"] == "на батарее, заряд 12%"

async def test_a_worker_too_old_to_send_a_word_still_says_something(app, monkeypatch):
    from services.render_farm.roster import Roster

    roster = Roster()
    roster.hello("laptop", capacity={"take": False, "reason": "on battery at 12%"})
    monkeypatch.setattr(api, "render_roster", roster)

    client, _ = app
    said = await (await client.get("/app/api/farm", headers=auth())).json()
    assert said["workers"][0]["reason"] == "on battery at 12%"

async def test_a_word_nobody_knows_falls_back_rather_than_showing_a_key(app, monkeypatch):
    from services.render_farm.roster import Roster

    roster = Roster()
    roster.hello("laptop", capacity={
        "take": False, "reason": "something new happened", "code": "invented",
    })
    monkeypatch.setattr(api, "render_roster", roster)

    client, _ = app
    said = await (await client.get("/app/api/farm", headers=auth())).json()
    assert said["workers"][0]["reason"] == "something new happened"
