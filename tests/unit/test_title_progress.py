from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers.profile.recent import _play_from_score
from db.database import Base
from db.models.map_attempt import UserMapAttempt
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from utils.title_progress import (
    _mod_set,
    _play_matches,
    evaluate_recent_plays,
    refresh_user_titles,
    unlock_title,
)
from utils.titles import TITLE_REGISTRY

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'titles.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def _user(factory) -> int:
    async with factory() as s:
        u = User(chat_id=-100, telegram_id=1, osu_username="alice", osu_user_id=1001, play_count=0)
        s.add(u)
        await s.commit()
        return u.id

def _attempt(uid, n, **kw):
    base = dict(user_id=uid, score_id=n, beatmap_id=10, pp=0.0, passed=True, rank="A",
                played_at=datetime(2026, 1, 1) + timedelta(minutes=n))
    base.update(kw)
    return UserMapAttempt(**base)

async def _unlocked(factory, uid) -> set:
    async with factory() as s:
        rows = await s.execute(select(UserTitleProgress.title_code).where(
            UserTitleProgress.user_id == uid, UserTitleProgress.unlocked.is_(True)))
        return {r[0] for r in rows.all()}

def test_joined_mods_are_read_in_pairs():
    assert _mod_set("HDTD") == {"HD", "TD"}
    assert _mod_set("HD,DT") == {"HD", "DT"}
    assert not _play_matches({"passed": True, "star_rating": 9.0, "rank": "X", "mods": "HDTD"},
                             min_sr=8.0, ranks=("X", "XH"), mods_all=["HD"], mods_any=["DT", "NC"])

def test_a_recent_play_names_its_map():
    play = _play_from_score({"beatmap": {"id": 77}, "mods": [{"acronym": "HD"}, {"acronym": "TD"}]})
    assert play["beatmap_id"] == 77
    assert _mod_set(play["mods"]) == {"HD", "TD"}

async def test_registering_is_enough_for_the_first_title(factory):
    uid = await _user(factory)
    async with factory() as s:
        user = await s.get(User, uid)
        await refresh_user_titles(user, s)
        await s.commit()
    assert "registered" in await _unlocked(factory, uid)

async def test_secret_titles_unlock_on_recalculation(factory):
    uid = await _user(factory)
    async with factory() as s:
        s.add(_attempt(uid, 1, score=1777777))
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        await refresh_user_titles(user, s)
        await s.commit()
    assert "magic7" in await _unlocked(factory, uid)

async def test_two_sessions_do_not_collide_on_new_rows(factory):
    uid = await _user(factory)
    async with factory() as first, factory() as second:
        u1 = await first.get(User, uid)
        u2 = await second.get(User, uid)
        await refresh_user_titles(u1, first)
        await first.commit()
        await refresh_user_titles(u2, second)
        await unlock_title(u2, "compare_50", second)
        await second.commit()
    async with factory() as s:
        n = (await s.execute(select(func.count()).select_from(UserTitleProgress)
                             .where(UserTitleProgress.user_id == uid))).scalar()
    assert n == len(TITLE_REGISTRY)
    assert "compare_50" in await _unlocked(factory, uid)

async def test_stuck_in_a_loop_unlocks_from_recent(factory):
    uid = await _user(factory)
    async with factory() as s:
        s.add_all([_attempt(uid, n, beatmap_id=42) for n in range(1, 16)])
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        newly = await evaluate_recent_plays(user, [{"passed": True, "beatmap_id": 42}], s)
        await s.commit()
    assert "repeat_15" in {td.code for td in newly}

async def test_classic_is_not_a_mask(factory):
    uid = await _user(factory)
    async with factory() as s:
        s.add_all([_attempt(uid, n, mods=m) for n, m in
                   enumerate(["CL", "HD,CL", "HR,CL", "DT,CL", "NM"], start=1)])
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        progress = {p["code"]: p for p in await refresh_user_titles(user, s)}
    assert progress["masks_5"]["current"] == 3
    assert not progress["masks_5"]["unlocked"]
