"""Unit tests for services.hps.anti_farm.compute_anti_farm_multiplier.

Plan: unified-giggling-tiger (step 6/9).

Covers:
  - Empty history → multiplier = 1.0
  - Same-map repeats → 0.7^N decay
  - Same-type ratio penalty kicks in above 50% share in 7d window
  - Composite respects the 0.3 floor
  - 7-day window honours `now` cutoff
  - Different beatmap / different type → no cross-talk
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
    COMPOSITE_FLOOR,
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
        assert breakdown["same_type_ratio_7d"] == 0.0
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
        # 0.7^1 = 0.7, no type share (different type)
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
        # Different type → only same_map kicks in. 0.7^3 ≈ 0.343
        assert b["same_map_count"] == 3
        assert mult == pytest.approx(SAME_MAP_BASE ** 3, rel=1e-3)

    @pytest.mark.asyncio
    async def test_only_approved_counts(self, session):
        # An unapproved submission must NOT count toward same_map_count.
        await _add_bounty(session, bounty_id="b1", beatmap_id=100, bounty_type="First FC")
        await _add_bounty(session, bounty_id="b2", beatmap_id=100, bounty_type="First FC")
        await _add_submission(session, user_id=1, bounty_id="b1", status="approved")
        await _add_submission(session, user_id=1, bounty_id="b2", status="pending")
        await session.commit()
        _, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=100, bounty_type="SS", now=NOW,
        )
        assert b["same_map_count"] == 1


class TestSameTypeRatio:
    @pytest.mark.asyncio
    async def test_under_50pct_no_penalty(self, session):
        # 5 approved subs in last 7d, 2 of type "Speed" → ratio 0.4 < 0.5.
        for i, bt in enumerate(["Mod", "Mod", "Mod", "SS", "SS"]):
            # Different beatmap_id each, so same_map_factor = 1.0
            await _add_bounty(session, bounty_id=f"b{i}", beatmap_id=200 + i, bounty_type=bt)
            await _add_submission(session, user_id=1, bounty_id=f"b{i}")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=999, bounty_type="Mod", now=NOW,
        )
        # 3/5 = 0.6 ratio for Mod, excess 0.1 → factor 1 - 0.3*0.1 = 0.97
        assert b["same_type_ratio_7d"] == pytest.approx(0.6)
        assert mult == pytest.approx(1.0 - 0.3 * 0.1, rel=1e-3)

    @pytest.mark.asyncio
    async def test_full_domination(self, session):
        # 10 subs all Mod → ratio 1.0, excess 0.5 → factor 1 - 0.15 = 0.85
        for i in range(10):
            await _add_bounty(session, bounty_id=f"b{i}", beatmap_id=300 + i, bounty_type="Mod")
            await _add_submission(session, user_id=1, bounty_id=f"b{i}")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=999, bounty_type="Mod", now=NOW,
        )
        assert b["same_type_ratio_7d"] == pytest.approx(1.0)
        assert b["same_type_factor"] == pytest.approx(0.85, rel=1e-3)
        assert mult == pytest.approx(0.85, rel=1e-3)


class TestWindow:
    @pytest.mark.asyncio
    async def test_old_submissions_excluded(self, session):
        # 10 Mod subs but ALL > 7 days old → ratio 0, no penalty.
        old = NOW - timedelta(days=10)
        for i in range(10):
            await _add_bounty(session, bounty_id=f"b{i}", beatmap_id=400 + i, bounty_type="Mod")
            await _add_submission(session, user_id=1, bounty_id=f"b{i}", submitted_at=old)
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=999, bounty_type="Mod", now=NOW,
        )
        assert b["same_type_ratio_7d"] == 0.0
        assert mult == 1.0


class TestComposite:
    @pytest.mark.asyncio
    async def test_floor_at_0_3(self, session):
        # 20 repeats on same map of same type → 0.7^20 ≈ 0.0008,
        # plus type ratio 1.0 → composite would be ~0.0007.
        # Floor must clamp to 0.3.
        for i in range(20):
            await _add_bounty(session, bounty_id=f"b{i}", beatmap_id=500, bounty_type="Mod")
            await _add_submission(session, user_id=1, bounty_id=f"b{i}")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=500, bounty_type="Mod", now=NOW,
        )
        assert b["same_map_count"] == 20
        assert mult == COMPOSITE_FLOOR

    @pytest.mark.asyncio
    async def test_different_users_isolated(self, session):
        # user 2 farms map 100; user 1 should see zero history.
        await _add_bounty(session, bounty_id="b1", beatmap_id=100, bounty_type="Mod")
        await _add_submission(session, user_id=2, bounty_id="b1")
        await session.commit()
        mult, b = await compute_anti_farm_multiplier(
            session, user_id=1, beatmap_id=100, bounty_type="Mod", now=NOW,
        )
        assert b["same_map_count"] == 0
        assert mult == 1.0
