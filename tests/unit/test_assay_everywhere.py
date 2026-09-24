from datetime import datetime, timezone

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from config import settings
from db.database import Base
from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from services.image.render.map_leaderboard import _pp_text
from services.leaderboard.service import build_map_leaderboard
from utils.osu import assay_service
from utils.osu.api_client import OsuApiClient

CHAT = -1001
MAP = 4242

@pytest_asyncio.fixture
async def service(monkeypatch):
    asked = []

    async def beatmap(request):
        body = await request.json()
        asked.append(("beatmap", body))
        rate = next((m.get("settings", {}).get("speed_change", 1.5) for m in body["mods"] if m["acronym"] == "DT"), 1.0)
        return web.json_response({"star_rating": 5.0 * rate, "max_combo": 1000})

    async def score(request):
        body = await request.json()
        asked.append(("score", body))
        return web.json_response({"pp": 123.456, "star_rating": 5.0, "map": {"max_combo": 1000}})

    app = web.Application()
    app.router.add_post("/v1/beatmap", beatmap)
    app.router.add_post("/v1/score", score)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(settings, "ASSAY_URL", str(server.make_url("")).rstrip("/"))
    monkeypatch.setattr(settings, "ASSAY_TOKEN", "")

    async def no_dates(self, beatmap_ids):
        return {}

    monkeypatch.setattr(OsuApiClient, "ranked_dates", no_dates)
    yield asked
    await assay_service.close()
    await server.close()

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def test_stars_with_mods_come_from_the_service_with_their_settings(service):
    client = OsuApiClient()
    slower = [{"acronym": "HD"}, {"acronym": "DT", "settings": {"speed_change": 1.2}}]
    assert await client.effective_sr(MAP, slower, 4.0, "c" * 32) == 6.0
    assert await client.effective_sr(MAP, "HDDT", 4.0) == 7.5
    assert await client.effective_sr(MAP, slower, 4.0, "c" * 32) == 6.0
    beatmaps = [body for kind, body in service if kind == "beatmap"]
    assert len(beatmaps) == 2, "the same mods were asked twice"
    assert beatmaps[0]["checksum"] == "c" * 32
    assert beatmaps[0]["mods"][1] == {"acronym": "DT", "settings": {"speed_change": 1.2}}

async def test_no_mods_or_only_classic_keeps_the_nominal_rating(service):
    client = OsuApiClient()
    assert await client.effective_sr(MAP, "", 4.0) == 4.0
    assert await client.effective_sr(MAP, [{"acronym": "CL"}], 4.0) == 4.0
    assert service == []

def _raw(score_id, *, pp, passed=True):
    return {
        "id": score_id, "pp": pp, "passed": passed, "accuracy": 0.97, "max_combo": 500,
        "rank": "A", "total_score": 900000, "mods": [{"acronym": "HD"}],
        "statistics": {"great": 480, "ok": 20, "miss": 1},
        "beatmap": {"id": MAP, "checksum": "d" * 32, "difficulty_rating": 5.0, "status": "loved"},
        "beatmapset": {"id": 1},
        "ended_at": "2026-09-01T12:00:00Z",
    }

async def test_a_play_osu_gave_no_pp_is_counted_once(service, factory):
    client = OsuApiClient()
    async with factory() as session:
        user = User(chat_id=CHAT, telegram_id=1, osu_user_id=2, osu_username="A")
        session.add(user)
        await session.flush()
        await client.sync_user_map_attempts(user, session, [
            _raw(1, pp=None), _raw(2, pp=250.0), _raw(3, pp=None, passed=False),
        ])
        await session.commit()
        await client.sync_user_map_attempts(user, session, [_raw(1, pp=None)])
        await session.commit()
        rows = {a.score_id: a for a in (await session.execute(select(UserMapAttempt))).scalars()}

    assert rows[1].pp_estimated == 123.46
    assert rows[2].pp_estimated is None and rows[2].pp == 250.0
    assert rows[3].pp_estimated is None
    scores = [body for kind, body in service if kind == "score"]
    assert len(scores) == 1, "a stored estimate was counted again"
    assert scores[0]["checksum"] == "d" * 32
    assert scores[0]["statistics"] == {"great": 480, "ok": 20, "miss": 1}
    assert scores[0]["mods"] == [{"acronym": "HD", "settings": {}}]

class _Osu:
    async def get_beatmap(self, beatmap_id):
        return {"id": beatmap_id, "status": "loved", "version": "Insane",
                "beatmapset": {"id": 1, "artist": "A", "title": "T", "covers": {}}}

async def test_the_map_leaderboard_shows_an_estimate_where_osu_gave_none(factory):
    async with factory() as session:
        a = User(chat_id=CHAT, telegram_id=1, osu_user_id=2, osu_username="A")
        b = User(chat_id=CHAT, telegram_id=3, osu_user_id=4, osu_username="B")
        session.add_all([a, b])
        await session.flush()
        played = datetime(2026, 9, 1, tzinfo=timezone.utc)
        session.add_all([
            UserMapAttempt(user_id=a.id, score_id=1, beatmap_id=MAP, pp=0.0, pp_estimated=321.5,
                           score=900, played_at=played),
            UserMapAttempt(user_id=b.id, score_id=2, beatmap_id=MAP, pp=0.0, score=800, played_at=played),
        ])
        await session.commit()
        result = await build_map_leaderboard(session, _Osu(), MAP, CHAT, sync=False)

    first, second = result.rows
    assert first["username"] == "A" and first["pp"] == 321.5 and first["pp_estimated"]
    assert second["pp"] == 0.0 and not second["pp_estimated"]
    assert _pp_text(first) == "~321.5"
    assert _pp_text(second) == "0.0"
