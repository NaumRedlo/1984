"""Tests for the weekly-pool regen guard + canonical bounty-type validation.

Covers:
  * generate_weekly_pool(force=False) is idempotent — when an active pool whose
    window still covers now exists, the existing pool is returned rather than a
    second one being made (audit #6).
  * force=True still rotates the pool.
  * _canonical_bounty_type rejects unknown types (audit #13 — edit path now
    mirrors the create path).

In-memory aiosqlite pattern mirrors test_weekly_generator_integration.py.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.bounty import Bounty  # noqa: F401  (registers on Base.metadata)
from db.models.duel_map_pool import DuelMapPool
from db.models.weekly_bounty_pool import WeeklyBountyPool
from services.bounty.weekly_generator import generate_weekly_pool
from bot.handlers.admin.bounty_utils import _canonical_bounty_type


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


async def _seed_maps(session, count: int = 60) -> None:
    for i in range(count):
        duel = 0.2 + (i * 4.8 / max(count - 1, 1))
        session.add(DuelMapPool(
            beatmap_id=10_000 + i, beatmapset_id=20_000 + i,
            title=f"song{i}", artist="artist", version="diff", creator="mapper",
            star_rating=duel * 2.0, bpm=180.0, length=180,
            ar=9.0, od=9.0, cs=4.0, hp_drain=6.0,
            w_aim=0.25, w_speed=0.25, w_acc=0.25, w_cons=0.25,
            aim_stars=duel, speed_stars=duel, acc_stars=duel, cons_stars=duel,
            map_type="aim", enabled=True,
        ))
    await session.flush()


class TestRegenGuard:
    async def test_force_false_is_idempotent(self, session):
        await _seed_maps(session)
        first = await generate_weekly_pool(session, force=False)
        await session.flush()

        # An active pool whose window covers now already exists → no new pool.
        second = await generate_weekly_pool(session, force=False)
        await session.flush()
        assert second.id == first.id

        active = (await session.execute(
            select(WeeklyBountyPool).where(WeeklyBountyPool.is_active == 1)
        )).scalars().all()
        assert len(active) == 1

    async def test_force_true_rotates(self, session):
        await _seed_maps(session)
        first = await generate_weekly_pool(session, force=True)
        await session.flush()
        second = await generate_weekly_pool(session, force=True)
        await session.flush()
        assert second.id != first.id
        assert second.week_number == first.week_number + 1
        # Still exactly one active pool afterwards.
        active = (await session.execute(
            select(WeeklyBountyPool).where(WeeklyBountyPool.is_active == 1)
        )).scalars().all()
        assert len(active) == 1
        assert active[0].id == second.id


class TestCanonicalBountyType:
    def test_known_types_canonicalised(self):
        assert _canonical_bounty_type("first fc") == "First FC"
        assert _canonical_bounty_type("  SNIPE ") == "Snipe"

    def test_unknown_type_rejected(self):
        assert _canonical_bounty_type("definitely-not-a-type") is None
        assert _canonical_bounty_type("") is None
