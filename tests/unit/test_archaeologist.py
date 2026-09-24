from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from utils.osu import api_client as api
from utils.osu.api_client import OsuApiClient
from utils.title_progress import _calc_archaeologist

OLD, NEW = 75, 4_000_000

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

@pytest_asyncio.fixture
def osu(monkeypatch):
    asked = []

    async def request(self, method, endpoint, params=None, **kwargs):
        assert endpoint == "beatmaps"
        ids = [b for _, b in params]
        asked.append(ids)
        dates = {OLD: "2008-03-01T00:00:00Z", NEW: "2025-01-01T00:00:00Z"}
        return {"beatmaps": [{"id": b, "beatmapset": {"ranked_date": dates.get(b)}} for b in ids]}

    monkeypatch.setattr(OsuApiClient, "_make_request", request)
    monkeypatch.setattr(api, "_RANKED_DATES", {})
    return asked

def _play(score_id, beatmap_id, *, passed=True, status="ranked"):
    return {
        "id": score_id, "pp": 100.0, "passed": passed, "accuracy": 0.95, "max_combo": 300,
        "rank": "A" if passed else "F", "total_score": 500000, "mods": [],
        "statistics": {"great": 290, "ok": 10},
        "beatmap": {"id": beatmap_id, "difficulty_rating": 4.0, "status": status},
        # osu! sends the compact set, which has no ranked_date
        "beatmapset": {"id": beatmap_id},
        "ended_at": "2026-09-01T12:00:00Z",
    }

async def _user(session):
    user = User(chat_id=-1, telegram_id=1, osu_user_id=2, osu_username="A")
    session.add(user)
    await session.flush()
    return user

async def test_a_pass_on_an_old_map_earns_it(factory, osu):
    async with factory() as session:
        user = await _user(session)
        await OsuApiClient().sync_user_map_attempts(user, session, [_play(1, OLD), _play(2, NEW)])
        await session.commit()
        assert await _calc_archaeologist(session, user.id) == 1
        rows = {a.beatmap_id: a.ranked_date for a in (await session.execute(select(UserMapAttempt))).scalars()}
    assert rows[OLD].year == 2008 and rows[NEW].year == 2025
    assert osu == [[OLD, NEW]]

async def test_a_fail_or_a_new_map_does_not(factory, osu):
    async with factory() as session:
        user = await _user(session)
        await OsuApiClient().sync_user_map_attempts(user, session, [_play(1, OLD, passed=False), _play(2, NEW)])
        await session.commit()
        assert await _calc_archaeologist(session, user.id) == 0

async def test_each_map_is_asked_once_and_a_known_date_survives_a_resync(factory, osu):
    async with factory() as session:
        user = await _user(session)
        client = OsuApiClient()
        await client.sync_user_map_attempts(user, session, [_play(1, OLD)])
        await client.sync_user_map_attempts(user, session, [_play(1, OLD), _play(3, OLD)])
        await session.commit()
        dates = [a.ranked_date for a in (await session.execute(select(UserMapAttempt))).scalars()]
    assert all(d is not None and d.year == 2008 for d in dates)
    assert osu == [[OLD]]

async def test_graveyard_maps_are_not_looked_up(factory, osu):
    async with factory() as session:
        user = await _user(session)
        await OsuApiClient().sync_user_map_attempts(user, session, [_play(1, OLD, status="graveyard")])
    assert osu == []
