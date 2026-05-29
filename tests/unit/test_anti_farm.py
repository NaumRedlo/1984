"""Unit tests for services.hps.anti_farm.compute_anti_farm_multiplier.

Plan: unified-giggling-tiger (step 6/9).

Simplified contract (2026-05-29):
  - No category penalty; only same-map repeat decay survives.
  - No composite floor; can decay to 0 on heavy grinding.

Covers:
  - Empty history → multiplier = 1.0
  - Same-map repeats → 0.7^N decay
  - Only approved submissions count
  - Different users / different beatmaps are isolated
  - Category-share queries are no-ops (kept for API compat)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.bounty import Bounty, Submission
from db.models.user import User  # noqa: F401  (register on metadata)

from services.hps.anti_farm import (
    compute_anti_farm_multiplier,
    SAME_MAP_BASE,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


NOW = datetime(2026, 5, 28, 12, 0, 0)


async def _add_bounty(sess, *, bounty_id: str, beatmap_id: int, bounty_type: str) -> None:
    sess.add(Bounty(
        bounty_id=bounty_id, bounty_type=bounty_type,
        title=f"B {bounty_id}", beatmap_id=beatmap_id,
        beatmap_title="t", star_rating=5.0, drain_time=200,
        created_by=1,
    ))


async def _add_submission(
    sess, *, user_id: int, bounty_id: str, status: str = "approved",
    submitted_at: datetime | None = None,
) -> None:
    sess.add(Submission(
        bounty_id=bounty_id, user_id=user_id, telegram_id=user_id,
        status=status, submitted_at=submitted_at or NOW - timedelta(hours=1),
    ))


# ── Tests ──────────────────────────────────────────────────────────────────


class TestEmptyHistory:
    @pytest.mark.asyncio
    async def test_no_submissions_returns_one(self, session):
        mult, breakdown = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=100,
            bounty_type="First FC", now=NOW,
        )
        assert mult == 1.0
        assert breakdown["same_map_count"] == 0
        assert breakdown["composite"] == 1.0


class TestSameMapDecay:
    @pytest.mark.asyncio
    async def test_single_repeat(self, session):
        await _add_bounty(session, bounty_id="b1", beatmap_id=100, bounty_type="First FC")
        await _add_submission(session, user_id=1, bounty_id="b1")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=100, bounty_type="SS", now=NOW,
        )
        assert b["same_map_count"] == 1
        assert b["same_map_factor"] == pytest.approx(SAME_MAP_BASE)
        assert mult == pytest.approx(SAME_MAP_BASE)

    @pytest.mark.asyncio
    async def test_three_repeats(self, session):
        for i in range(3):
            await _add_bounty(session, bounty_id=f"b{i}", beatmap_id=100, bounty_type="Mod")
            await _add_submission(session, user_id=1, bounty_id=f"b{i}")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=100, bounty_type="Accuracy", now=NOW,
        )
        assert b["same_map_count"] == 3
        assert mult == pytest.approx(SAME_MAP_BASE ** 3, rel=1e-3)

    @pytest.mark.asyncio
    async def test_only_approved_counts(self, session):
        await _add_bounty(session, bounty_id="b1", beatmap_id=100, bounty_type="First FC")
        await _add_bounty(session, bounty_id="b2", beatmap_id=100, bounty_type="First FC")
        await _add_submission(session, user_id=1, bounty_id="b1", status="approved")
        await _add_submission(session, user_id=1, bounty_id="b2", status="pending")
        await session.commit()
        _, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=100, bounty_type="SS", now=NOW,
        )
        assert b["same_map_count"] == 1

    @pytest.mark.asyncio
    async def test_heavy_grind_no_floor(self, session):
        # 20 repeats on same map → 0.7^20 ≈ 0.0008. No floor: must reach near-zero.
        for i in range(20):
            await _add_bounty(session, bounty_id=f"b{i}", beatmap_id=500, bounty_type="Mod")
            await _add_submission(session, user_id=1, bounty_id=f"b{i}")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=500, bounty_type="Mod", now=NOW,
        )
        assert b["same_map_count"] == 20
        # Should be well below the old floor of 0.3 — that's the whole point.
        assert mult < 0.01


class TestCategorySharePeNotApplied:
    @pytest.mark.asyncio
    async def test_full_domination_unpenalized(self, session):
        # 10 subs all Mod on DIFFERENT maps → no same-map decay, no category decay.
        for i in range(10):
            await _add_bounty(session, bounty_id=f"b{i}", beatmap_id=300 + i, bounty_type="Mod")
            await _add_submission(session, user_id=1, bounty_id=f"b{i}")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=999, bounty_type="Mod", now=NOW,
        )
        # Specializing in Mod bounties is free now.
        assert mult == 1.0


class TestIsolation:
    @pytest.mark.asyncio
    async def test_different_users_isolated(self, session):
        await _add_bounty(session, bounty_id="b1", beatmap_id=100, bounty_type="Mod")
        await _add_submission(session, user_id=2, bounty_id="b1")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=100, bounty_type="Mod", now=NOW,
        )
        assert b["same_map_count"] == 0
        assert mult == 1.0

    @pytest.mark.asyncio
    async def test_different_beatmaps_isolated(self, session):
        await _add_bounty(session, bounty_id="b1", beatmap_id=100, bounty_type="Mod")
        await _add_submission(session, user_id=1, bounty_id="b1")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=200, bounty_type="Mod", now=NOW,
        )
        assert b["same_map_count"] == 0
        assert mult == 1.0
