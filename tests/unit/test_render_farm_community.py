import types
from datetime import datetime, timedelta

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.best_score import UserBestScore
from db.models.leaderboard_snapshot import LeaderboardSnapshot
from db.models.map_attempt import UserMapAttempt
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from services.leaderboard.periods import current_period_key
from services.render_farm import community, http, invites

CHAT = -1001
NOW = datetime(2026, 9, 23, 12, 0)

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

def _user(tg, name, **kw):
    base = dict(chat_id=CHAT, telegram_id=tg, osu_username=name, osu_user_id=tg * 10,
                player_pp=1000, global_rank=50_000, country="ru", accuracy=97.0, play_count=1000,
                play_time=7200, ranked_score=1_000_000, total_hits=400_000, rank="Candidate",
                best_scores_baseline_at=NOW - timedelta(days=30))
    base.update(kw)
    return User(**base)

def _best(user_id, score_id, pp, **kw):
    base = dict(user_id=user_id, score_id=score_id, beatmap_id=score_id, beatmapset_id=score_id + 1,
                pp=pp, accuracy=98.5, rank="S", mods="HD,DT", artist="xi", title="FREEDOM DiVE",
                version="FOUR DIMENSIONS", creator="Nakagawa-Kanon", star_rating=7.2, max_combo=1983,
                map_max_combo=2000, is_fc=False, created_at=NOW - timedelta(days=60))
    base.update(kw)
    return UserBestScore(**base)

async def _seed(factory):
    async with factory() as s:
        naum = _user(7, "NaumRedlo", player_pp=9870, global_rank=11_402)
        koto = _user(8, "kotofey", player_pp=12480, global_rank=3_402)
        lumen = _user(9, "lumen", player_pp=3402)
        s.add_all([naum, koto, lumen])
        await s.flush()
        s.add_all([
            _best(naum.id, 1, 412.6),
            _best(naum.id, 2, 398.1, created_at=NOW - timedelta(days=2)),
            _best(koto.id, 3, 611.2, rank="XH", mods="HD,HR,CL"),
            UserMapAttempt(user_id=koto.id, score_id=10, beatmap_id=5, pp=530.0, accuracy=99.4, rank="S",
                           mods="HD,HR", artist="Phoneboy", title="Nevermind", version="Insane",
                           played_at=NOW - timedelta(minutes=3), passed=True),
            UserMapAttempt(user_id=naum.id, score_id=11, beatmap_id=6, pp=312.0, accuracy=96.7, rank="A",
                           mods="HD,DT", artist="Dj Grimoire", title="Astral Quantization", version="Nattu",
                           played_at=NOW - timedelta(minutes=9), passed=True),
            UserTitleProgress(user_id=naum.id, title_code="wysi", current_value=1, unlocked=True,
                              unlocked_at=NOW - timedelta(days=1)),
            UserTitleProgress(user_id=naum.id, title_code="not_a_title", current_value=1, unlocked=True,
                              unlocked_at=NOW - timedelta(days=1)),
            LeaderboardSnapshot(tenant_chat_id=CHAT, user_id=naum.id, period_key=current_period_key(NOW),
                                player_pp=3000, accuracy=97.0, play_count=900, play_time=7000,
                                ranked_score=900_000, total_hits=390_000,
                                prev_positions='{"pp": 4, "play_count": 2}'),
            LeaderboardSnapshot(tenant_chat_id=CHAT, user_id=lumen.id, period_key=current_period_key(NOW),
                                player_pp=5000, accuracy=97.0, play_count=1000, play_time=7200,
                                ranked_score=1_000_000, total_hits=400_000),
        ])
        naum.active_title_code = "wysi"
        await s.commit()
        return naum.id, koto.id, lumen.id

async def test_a_chat_comes_out_with_its_people_ranked_by_pp(factory):
    naum, koto, lumen = await _seed(factory)
    async with factory() as s:
        got = await community.gather(s, CHAT, 7, now=NOW)
    assert [p["name"] for p in got["people"]] == ["kotofey", "NaumRedlo", "lumen"]
    me = next(p for p in got["people"] if p["you"])
    assert me["name"] == "NaumRedlo" and me["country"] == "RU" and me["hours"] == 2
    assert me["title"] == "wysi" and me["titles"] == ["wysi"]
    assert [play["pp"] for play in me["top"]] == [412.6, 398.1]
    assert me["top"][0]["mods"] == ["HD", "DT"]
    koto_top = next(p for p in got["people"] if p["name"] == "kotofey")["top"][0]
    assert koto_top["grade"] == "SS" and koto_top["mods"] == ["HD", "HR"]

async def test_the_week_s_moves_come_from_its_anchors(factory):
    naum, koto, lumen = await _seed(factory)
    async with factory() as s:
        got = await community.gather(s, CHAT, 7, now=NOW)
    me = next(p for p in got["people"] if p["you"])
    them = next(p for p in got["people"] if p["name"] == "lumen")
    assert me["moved"][0] == 1 and them["moved"][0] == -1
    assert me["gained"][0] == 6870.0
    climbs = [h for h in got["happened"] if h["kind"] == "climb"]
    assert climbs == [{"who": naum, "kind": "climb", "board": "pp", "from": 3, "to": 2, "at": climbs[0]["at"]}]

async def test_the_last_week_s_places_and_the_collecting_state_come_along(factory):
    naum, koto, lumen = await _seed(factory)
    async with factory() as s:
        got = await community.gather(s, CHAT, 7, now=NOW)
    me = next(p for p in got["people"] if p["you"])
    koto_was = next(p for p in got["people"] if p["name"] == "kotofey")["was"]
    assert me["was"] == [4, 0, 2, 0, 0, 0]
    assert koto_was == [0] * 6
    assert got["collecting"] is False
    assert got["week_began"] > 0

def test_a_broken_closing_record_reads_as_no_places():
    class Anchor:
        prev_positions = "not json"
    assert community._was(Anchor()) == [0] * 6
    assert community._was(None) == [0] * 6

async def test_plays_arrive_newest_first(factory):
    naum, koto, lumen = await _seed(factory)
    async with factory() as s:
        got = await community.gather(s, CHAT, 7, now=NOW)
    assert [play["who"] for play in got["live"]] == [koto, naum]
    assert got["live"][0]["map"]["title"] == "Nevermind"
    assert got["live"][0]["at"] > got["live"][1]["at"]

async def test_only_new_top_plays_and_titles_are_happenings(factory):
    naum, koto, lumen = await _seed(factory)
    async with factory() as s:
        got = await community.gather(s, CHAT, 7, now=NOW)
    kinds = sorted((h["kind"], h["who"]) for h in got["happened"])
    assert ("top_play", naum) in kinds and ("title", naum) in kinds
    top = next(h for h in got["happened"] if h["kind"] == "top_play")
    assert top["place"] == 2 and top["play"]["pp"] == 398.1

async def test_the_titles_come_with_the_catalogue_in_both_languages(factory):
    catalogue = community.titles_catalogue()
    codes = [t["code"] for t in catalogue]
    assert "registered" in codes and "wysi" in codes
    assert all(t["name_ru"] and t["about_ru"] for t in catalogue)
    rarities = [t["rarity"] for t in catalogue]
    assert rarities == sorted(rarities, key=["common", "uncommon", "rare", "epic", "legendary", "mythic", "secret"].index)

async def test_the_person_s_own_profile_has_recent_plays(factory):
    await _seed(factory)
    async with factory() as s:
        me = await community.own(s, 7, CHAT, now=NOW)
        nobody = await community.own(s, 99, CHAT, now=NOW)
    assert me["name"] == "NaumRedlo" and me["you"] is True
    assert [play["map"]["title"] for play in me["recent"]] == ["Astral Quantization"]
    assert nobody is None

async def test_the_dossier_has_its_weeks_its_days_and_its_titles_dates(factory):
    await _seed(factory)
    async with factory() as s:
        me = await community.own(s, 7, CHAT, now=NOW)
    assert me["history"][0]["pp"] == 3000.0 and me["history"][0]["week"] == current_period_key(NOW)
    assert me["activity"] == [{"day": (NOW - timedelta(minutes=9)).date().isoformat(), "n": 1}]
    assert set(me["title_dates"]) == {"wysi"}
    assert me["top"][0]["counts"] == [None, None, None, None]

def test_friends_are_read_in_either_shape_online_first():
    users = [
        {"id": 1, "username": "moonlit", "country_code": "jp", "is_online": False,
         "last_visit": "2026-09-23T10:00:00+00:00", "statistics": {"pp": 7000.4, "global_rank": 20000}},
        {"id": 2, "username": "Riv3r", "country_code": "RU", "is_online": True, "statistics": {"pp": 9000, "global_rank": 9000}},
    ]
    listed = community.friends_from(users)
    assert [f["name"] for f in listed] == ["Riv3r", "moonlit"]
    assert listed[1]["country"] == "JP" and listed[1]["pp"] == 7000 and listed[1]["seen"] == 1790157600
    relations = [{"target": users[1], "mutual": True}]
    assert community.friends_from(relations)[0]["mutual"] is True

class _Bot:
    async def get_chat(self, chat_id):
        return types.SimpleNamespace(title="Osu Squad", full_name="", photo=None, username="", first_name="", last_name="")

    async def get_chat_member(self, chat_id, telegram_id):
        return types.SimpleNamespace(status="member" if chat_id == CHAT else "left")

@pytest_asyncio.fixture
async def served(monkeypatch, factory):
    import db.database

    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "a-shared-secret")
    monkeypatch.setattr(db.database, "AsyncSessionFactory", factory)
    token = "a-personal-token-of-sixty-four-characters-more-or-less-long-y"
    invites.remember(token, invites.Owner(7, "Naum"))
    http.set_bot(_Bot())
    app = web.Application()
    app.add_routes(http.make_routes())
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    await client.close()
    http.set_bot(None)
    invites.forget(invites.digest(token))

async def test_the_machine_gets_its_person_s_group_without_naming_it(served, factory):
    await _seed(factory)
    client, mine = served
    got = await (await client.get("/render/community", headers=mine)).json()
    assert got["chat"] == CHAT and got["group"] == "Osu Squad"
    assert len(got["people"]) == 3 and got["me"]["name"] == "NaumRedlo"

async def test_a_group_the_person_is_not_in_is_refused(served, factory):
    await _seed(factory)
    client, mine = served
    assert (await client.get("/render/community?chat=-5", headers=mine)).status == 403

async def test_a_member_opens_another_member_s_dossier(served, factory):
    naum, koto, lumen = await _seed(factory)
    client, mine = served
    reply = await client.get(f"/render/community/person?chat={CHAT}&id={lumen}", headers=mine)
    assert reply.status == 200
    them = await reply.json()
    assert them["name"] == "lumen" and them["you"] is False
    assert them["history"][0]["pp"] == 5000.0
    assert (await client.get(f"/render/community/person?chat={CHAT}&id=999", headers=mine)).status == 404
    assert (await client.get(f"/render/community/person?chat=-5&id={lumen}", headers=mine)).status == 403

async def test_friends_need_a_linked_osu_account(served, factory):
    client, mine = served
    reply = await client.get("/render/me/friends", headers=mine)
    assert reply.status == 409 and (await reply.json())["need"] == "link"

class _Osu:
    def __init__(self):
        self.asked = 0

    async def get_user_extended_data(self, osu_id):
        self.asked += 1
        return {
            "rank_history": [15400, 15300, 15234], "level": 102, "level_progress": 45, "country_rank": 412,
            "maximum_combo": 3421, "replays_watched": 234, "total_maps": 15392, "is_online": False,
            "grade_counts": {"a": 120, "s": 340, "sh": 90, "ss": 45, "ssh": 12},
            "join_date": "2018-05-12T00:00:00+00:00", "last_visit": "2026-09-23T10:00:00+00:00",
            "country_name": "Russian Federation", "total_score": 9876543210,
        }

    async def get_beatmap(self, beatmap_id):
        return None

async def test_the_card_carries_what_the_bots_card_draws(served, factory, monkeypatch):
    await _seed(factory)
    client, mine = served
    osu = _Osu()
    monkeypatch.setattr(http, "_osu", osu)
    http._card_cache.clear()
    reply = await client.get("/render/me/card", headers=mine)
    assert reply.status == 200, await reply.text()
    card = await reply.json()
    assert card["username"] == "NaumRedlo" and card["pp"] == 9870 and card["global_rank"] == 11402
    assert card["country_rank"] == 412 and card["level_progress"] == 45
    assert card["grade_counts"]["ssh"] == 12 and card["rank_history"][-1] == 15234
    assert [score["pp"] for score in card["top_scores"]] == [412.6, 398.1]
    assert card["title_code"] == "wysi" and card["play_seconds"] == 7200
    assert (await client.get("/render/me/card", headers=mine)).status == 200
    assert osu.asked == 1, "a card asked for again within minutes comes from memory"

async def test_without_osu_there_is_no_card(served, factory, monkeypatch):
    await _seed(factory)
    client, mine = served
    monkeypatch.setattr(http, "_osu", None)
    assert (await client.get("/render/me/card", headers=mine)).status == 503

def test_a_card_is_written_in_plain_json():
    from datetime import datetime as moment

    said = http._plain({"when": moment(2026, 9, 23, 12, 0), "colour": (1, 2, 3), "nested": [{"n": None}]})
    assert said == {"when": "2026-09-23T12:00:00", "colour": [1, 2, 3], "nested": [{"n": None}]}
