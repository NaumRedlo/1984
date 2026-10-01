import types
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.map_attempt import UserMapAttempt
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from services.render_farm import community, http, invites, maps

CHAT = -1001
OTHER = -2002
MAP = 4242
NOW = datetime(2026, 10, 1, 12, 0)

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

def _user(chat, tg, name, **kw):
    base = dict(chat_id=chat, telegram_id=tg, osu_username=name, osu_user_id=tg * 10, player_pp=1000,
                global_rank=50_000, country="ru", accuracy=97.0, play_count=1000, play_time=7200,
                ranked_score=1_000_000, total_hits=400_000, rank="Candidate")
    base.update(kw)
    return User(**base)

def _attempt(player_id, score_id, pp, *, score=0, passed=True, status="ranked", minutes=10, **kw):
    base = dict(player_id=player_id, score_id=score_id, beatmap_id=MAP, beatmapset_id=77, pp=pp, score=score,
                accuracy=98.0, rank="S", mods="HD,DT", artist="xi", title="FREEDOM DiVE", version="FOUR DIMENSIONS",
                creator="Nakagawa-Kanon", star_rating=7.2, bpm=222.22, length=258, max_combo=1800, map_max_combo=2000,
                count_300=1700, count_100=12, count_50=0, count_miss=1, status=status, passed=passed,
                played_at=NOW - timedelta(minutes=minutes))
    base.update(kw)
    return UserMapAttempt(**base)

async def _seed(factory, *, status="ranked"):
    async with factory() as s:
        naum, koto, lumen, far = _user(CHAT, 7, "NaumRedlo"), _user(CHAT, 8, "kotofey"), _user(CHAT, 9, "lumen"), _user(OTHER, 10, "elsewhere")
        s.add_all([naum, koto, lumen, far])
        await s.flush()
        s.add_all([
            _attempt(naum.player_id, 1, 300.0, score=900_000, minutes=600, status=status),
            _attempt(naum.player_id, 2, 412.6, score=700_000, minutes=30, status=status),
            _attempt(naum.player_id, 3, 0.0, score=1_500_000, passed=False, minutes=5, rank="F", status=status),
            _attempt(koto.player_id, 4, 380.0, score=1_100_000, minutes=120, status=status),
            _attempt(lumen.player_id, 5, 0.0, score=10_000, passed=False, minutes=3, rank="F", status=status),
            _attempt(far.player_id, 6, 999.0, score=9_000_000, status=status),
            UserMapAttempt(player_id=koto.player_id, score_id=7, beatmap_id=MAP + 1, pp=500.0, passed=True, played_at=NOW),
        ])
        await s.commit()
        return naum.id, koto.id, lumen.id

async def test_a_ranked_map_lists_each_player_once_by_their_best_pp(factory):
    naum, koto, _ = await _seed(factory)
    async with factory() as s:
        body = await maps.board(s, None, MAP, CHAT, 8, now=NOW)
    assert body["metric"] == "pp" and body["plays"] == 5 and body["players"] == 3
    assert [(row["name"], row["place"], row["pp"], row["id"]) for row in body["rows"]] == [("NaumRedlo", 1, 412.6, 2), ("kotofey", 2, 380.0, 4)]
    assert [row["you"] for row in body["rows"]] == [False, True]
    first = body["rows"][0]
    assert first["who"] == naum and first["score"] == 700_000 and first["counts"] == [1700, 12, 0, 1]
    assert body["map"]["title"] == "FREEDOM DiVE" and body["map"]["length"] == 258 and body["map"]["status"] == "ranked"
    assert [record["name"] for record in body["records"]] == ["NaumRedlo", "kotofey", "NaumRedlo"], "the record did not pass from hand to hand"
    assert body["records"][0]["pp"] == pytest.approx(412.6)

async def test_a_loved_map_is_ranked_by_score_and_a_failed_play_takes_no_place(factory):
    await _seed(factory, status="loved")
    async with factory() as s:
        body = await maps.board(s, None, MAP, CHAT, 7, now=NOW)
    assert body["metric"] == "score"
    assert [(row["name"], row["score"]) for row in body["rows"]] == [("kotofey", 1_100_000), ("NaumRedlo", 900_000)]

async def test_a_map_nobody_played_comes_back_empty(factory):
    await _seed(factory)
    async with factory() as s:
        body = await maps.board(s, None, 5, CHAT, 7, now=NOW)
    assert body["rows"] == [] and body["map"] is None and body["plays"] == 0 and body["records"] == []

async def test_a_play_carries_its_score_and_a_guess_at_pp_when_it_gave_none(factory):
    async with factory() as s:
        row = _attempt(1, 9, 0.0, score=123_456, pp_estimated=211.337)
        said = community._play(row, when=row.played_at)
        assert said["id"] == 9 and said["score"] == 123_456 and said["pp_if"] == 211.34
        assert community._play(_attempt(1, 10, 300.0, pp_estimated=280.0))["pp_if"] is None

async def test_the_community_counts_who_holds_each_title_across_every_chat(factory):
    naum, koto, lumen = await _seed(factory)
    async with factory() as s:
        far = (await s.execute(User.__table__.select().where(User.chat_id == OTHER))).first()
        naum, koto = (await s.get(User, naum)).player_id, (await s.get(User, koto)).player_id
        s.add_all([
            UserTitleProgress(player_id=naum, title_code="wysi", current_value=1, unlocked=True, unlocked_at=NOW - timedelta(days=2)),
            UserTitleProgress(player_id=naum, title_code="perfectionist", current_value=40, unlocked=False),
            UserTitleProgress(player_id=far.player_id, title_code="wysi", current_value=1, unlocked=True, unlocked_at=NOW),
            UserTitleProgress(player_id=koto, title_code="not_a_title", current_value=1, unlocked=True, unlocked_at=NOW),
        ])
        await s.commit()
        body = await community.gather(s, CHAT, 7, now=NOW)
        mine = await community.own(s, 7, CHAT, now=NOW)
    assert body["title_holders"] == {"players": 4, "held": {"wysi": 2}}
    dated = {card["name"]: card["earned"] for card in body["people"]}
    assert set(dated["NaumRedlo"]) == {"wysi"} and dated["kotofey"] == {}
    assert mine["title_progress"] == {"wysi": 1, "perfectionist": 40}

class _Bot:
    async def get_chat(self, chat_id):
        return types.SimpleNamespace(title="Osu Squad", full_name="", photo=None, username="", first_name="", last_name="")

    async def get_chat_member(self, chat_id, telegram_id):
        return types.SimpleNamespace(status="member" if (chat_id, telegram_id) == (CHAT, 7) else "left")

@pytest_asyncio.fixture
async def served(monkeypatch, factory):
    import db.database

    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "a-shared-secret")
    monkeypatch.setattr(db.database, "AsyncSessionFactory", factory)
    token = "a-personal-token-of-sixty-four-characters-more-or-less-long-y"
    invites.remember(token, invites.Owner(7, "Naum"))
    http.set_bot(_Bot())
    http.set_osu(None)
    app = web.Application()
    app.add_routes(http.make_routes())
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    await client.close()
    http.set_bot(None)
    invites.forget(invites.digest(token))

async def test_the_app_reads_the_board_of_a_map_in_its_own_chat(served, factory):
    client, headers = served
    await _seed(factory)
    answer = await client.get(f"/render/maps/{MAP}/board?chat={CHAT}", headers=headers)
    assert answer.status == 200
    body = await answer.json()
    assert [row["name"] for row in body["rows"]] == ["NaumRedlo", "kotofey"] and body["rows"][0]["you"] is True
    assert (await client.get("/render/maps/abc/board", headers=headers)).status == 400
    assert (await client.get(f"/render/maps/{MAP}/board?chat={OTHER}", headers=headers)).status == 403

class _Osu:
    def __init__(self, beatmap, scores=()):
        self.beatmap = beatmap
        self.scores = list(scores)
        self.looked = 0

    async def get_beatmap(self, beatmap_id):
        self.looked += 1
        return self.beatmap

    async def get_beatmap_scores(self, beatmap_id, limit=50):
        return [dict(score) for score in self.scores]

    async def get_user_beatmap_scores(self, beatmap_id, osu_user_id, oauth_token=None):
        return []

    async def effective_sr(self, *args, **kwargs):
        return None

    async def _fill_ranked_dates_quietly(self, session, player_id):
        return None

    async def sync_user_map_attempts(self, user_model, session, raw_scores):
        from utils.osu.api_client import OsuApiClient
        return await OsuApiClient.sync_user_map_attempts(self, user_model, session, raw_scores)

FULL = {"id": MAP, "version": "FOUR DIMENSIONS", "difficulty_rating": 7.21, "bpm": 222.22, "total_length": 258, "max_combo": 2385,
        "status": "ranked", "beatmapset": {"id": 77, "artist": "xi", "title": "FREEDOM DiVE", "creator": "Nakagawa-Kanon"}}

@pytest.fixture
def fresh_lookups(monkeypatch):
    from services.leaderboard import service
    monkeypatch.setattr(service, "_beatmap_facts", {})
    monkeypatch.setattr(service, "_sync_cooldown", {})
    return service

async def test_the_map_itself_says_its_longest_combo_and_is_asked_once(factory, fresh_lookups):
    async with factory() as s:
        naum = _user(CHAT, 7, "NaumRedlo")
        s.add(naum)
        await s.flush()
        s.add(_attempt(naum.player_id, 1, 300.0, title="", artist="", version="", bpm=None, length=None, map_max_combo=None, star_rating=None, status=None))
        await s.commit()
    osu = _Osu(FULL)
    async with factory() as s:
        body = await maps.board(s, osu, MAP, CHAT, 7, now=NOW)
        again = await maps.board(s, osu, MAP, CHAT, 7, sync=False, now=NOW)
    about = body["map"]
    assert (about["title"], about["version"], about["max_combo"], about["bpm"], about["length"], about["stars"]) == ("FREEDOM DiVE", "FOUR DIMENSIONS", 2385, 222.2, 258, 7.21)
    assert about["status"] == "ranked" and body["metric"] == "pp"
    assert again["map"] == about and osu.looked == 1

async def test_scores_of_a_map_that_come_bare_do_not_wipe_what_a_play_already_knows(factory, fresh_lookups):
    async with factory() as s:
        naum = _user(CHAT, 7, "NaumRedlo")
        s.add(naum)
        await s.flush()
        s.add(_attempt(naum.player_id, 1, 300.0))
        await s.commit()
    bare = {"id": 1, "user_id": 70, "pp": 300.0, "accuracy": 0.98, "max_combo": 1800, "rank": "S", "mods": [], "statistics": {}, "passed": True}
    async with factory() as s:
        await maps.board(s, _Osu(None, [bare]), MAP, CHAT, 7, now=NOW)
        kept = (await s.execute(select(UserMapAttempt).where(UserMapAttempt.score_id == 1))).scalar_one()
        assert (kept.title, kept.version, kept.bpm, kept.length, kept.map_max_combo, kept.status) == ("FREEDOM DiVE", "FOUR DIMENSIONS", 222.22, 258, 2000, "ranked")
    fresh_lookups._sync_cooldown.clear()
    newer = dict(bare, id=2, pp=310.0)
    async with factory() as s:
        body = await maps.board(s, _Osu(FULL, [newer]), MAP, CHAT, 7, now=NOW)
        made = (await s.execute(select(UserMapAttempt).where(UserMapAttempt.score_id == 2))).scalar_one()
        assert (made.title, made.version, made.map_max_combo, made.length) == ("FREEDOM DiVE", "FOUR DIMENSIONS", 2385, 258)
    assert body["map"]["max_combo"] == 2385

async def test_without_osu_the_map_is_told_from_the_plays_that_know_it(factory, fresh_lookups):
    async with factory() as s:
        naum = _user(CHAT, 7, "NaumRedlo")
        s.add(naum)
        await s.flush()
        s.add_all([
            _attempt(naum.player_id, 1, 300.0, minutes=600),
            _attempt(naum.player_id, 2, 310.0, minutes=5, title="", artist="", version="", bpm=None, length=None, map_max_combo=None, star_rating=None, status=None),
        ])
        await s.commit()
    async with factory() as s:
        body = await maps.board(s, None, MAP, CHAT, 7, now=NOW)
    about = body["map"]
    assert (about["title"], about["bpm"], about["length"], about["max_combo"], about["status"]) == ("FREEDOM DiVE", 222.2, 258, 2000, "ranked")
