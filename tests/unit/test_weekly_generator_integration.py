"""Integration tests for services.bounty.weekly_generator.

Plan: unified-giggling-tiger.

Pattern is borrowed from test_duel_user_skill.py — in-memory aiosqlite
database with Base.metadata.create_all so we exercise real SQLAlchemy ORM
flows without hitting any migration scripts.

The schema covered here:
  users, bounties, duel_map_pool, weekly_bounty_pool.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
# Importing models registers them on Base.metadata.
from db.models.bounty import Bounty  # noqa: F401
from db.models.duel_map_pool import DuelMapPool
from db.models.user import User
from db.models.weekly_bounty_pool import WeeklyBountyPool
from services.bounty.weekly_generator import generate_weekly_pool


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_maps(session, count: int = 60) -> None:
    """Seed `count` maps with star_rating spread across the full range.

    TIER_DUEL_RANGES (June 2026, SR scale): C=[2.0,4.5), B=[4.5,7.0), A=[7.0,10.0).
    sr = duel * 2.0 so the 60 maps cover SR ≈ 0.4..10.0 — each tier gets
    ≥9 eligible maps so generate_weekly_pool can fill every slot.
    """
    for i in range(count):
        duel = 0.2 + (i * 4.8 / max(count - 1, 1))   # 0.2 .. 5.0
        sr = duel * 2.0  # cosmetic — star_rating is on a 2× scale of DUEL
        session.add(DuelMapPool(
            beatmap_id=10_000 + i,
            beatmapset_id=20_000 + i,
            title=f"song{i}",
            artist="artist",
            version="diff",
            creator="mapper",
            star_rating=sr,
            bpm=180.0,
            length=180 if i % 10 else 700,  # every 10th map is a marathon
            ar=9.0, od=9.0, cs=4.0, hp_drain=6.0,
            w_aim=0.25, w_speed=0.25, w_acc=0.25, w_cons=0.25,
            aim_stars=duel, speed_stars=duel,
            acc_stars=duel + (1.5 if i % 5 == 0 else 0),
            cons_stars=duel,
            map_type="aim",
            enabled=True,
        ))
    await session.flush()


async def _seed_users(session, tg_id_base: int = 1000) -> dict[str, User]:
    """Seed one user per HPS tier. Returns dict keyed by tier letter."""
    users = {
        "C": User(chat_id=-100, telegram_id=tg_id_base + 1, osu_username="cand", hps_points=100),
        "B": User(chat_id=-100, telegram_id=tg_id_base + 2, osu_username="insp", hps_points=900),
        "A": User(chat_id=-100, telegram_id=tg_id_base + 3, osu_username="bb",   hps_points=3500),
    }
    for u in users.values():
        session.add(u)
    await session.flush()
    return users


# ── Generator end-to-end ────────────────────────────────────────────────────

class TestGenerateWeeklyPool:
    async def test_creates_24_bounties_4_tiers(self, session):
        await _seed_maps(session)
        await _seed_users(session)

        pool = await generate_weekly_pool(session)
        await session.flush()

        rows = (await session.execute(
            select(Bounty).where(Bounty.source == "auto")
        )).scalars().all()
        assert len(rows) == 24, f"expected 24 auto-bounties, got {len(rows)}"

        by_tier: dict[str, int] = {}
        for b in rows:
            by_tier[b.tier] = by_tier.get(b.tier, 0) + 1
        assert by_tier == {"C": 6, "B": 6, "A": 6, "Open": 6}

        # All link back to the freshly-created pool.
        assert all(b.week_id == pool.id for b in rows)

    async def test_sets_pool_is_active(self, session):
        await _seed_maps(session)
        pool = await generate_weekly_pool(session)
        await session.flush()
        assert pool.is_active == 1
        assert pool.week_number == 1

    async def test_closes_previous_pool(self, session):
        await _seed_maps(session)
        first = await generate_weekly_pool(session)
        await session.flush()
        first_id = first.id

        second = await generate_weekly_pool(session)
        await session.flush()
        assert second.id != first_id
        assert second.week_number == first.week_number + 1

        # Refresh first row to see the post-update state.
        refreshed_first = (await session.execute(
            select(WeeklyBountyPool).where(WeeklyBountyPool.id == first_id)
        )).scalar_one()
        assert refreshed_first.is_active == 0

        # Auto bounties from first pool are expired.
        old = (await session.execute(
            select(Bounty).where(
                Bounty.source == "auto", Bounty.week_id == first_id
            )
        )).scalars().all()
        assert all(b.status == "expired" for b in old)
        # New pool has 24 active bounties.
        new = (await session.execute(
            select(Bounty).where(
                Bounty.source == "auto", Bounty.week_id == second.id
            )
        )).scalars().all()
        assert len(new) == 24
        assert all(b.status == "active" for b in new)

    async def test_assigns_tiers_to_all_users(self, session):
        await _seed_maps(session)
        users = await _seed_users(session)

        await generate_weekly_pool(session)
        await session.flush()

        # Re-fetch each user to get the snapshot value.
        for tier, u in users.items():
            refreshed = (await session.execute(
                select(User).where(User.id == u.id)
            )).scalar_one()
            assert refreshed.weekly_tier == tier
            assert refreshed.weekly_tier_set_at is not None

    async def test_manual_bounties_untouched(self, session):
        await _seed_maps(session)

        manual = Bounty(
            bounty_id="manual/preserved",
            bounty_type="First FC",
            title="hand-made",
            beatmap_id=99_999,
            beatmap_title="manual map",
            star_rating=5.0,
            drain_time=180,
            created_by=12345,
            source="manual",
            status="active",
        )
        session.add(manual)
        await session.flush()

        # Two generation cycles — manual should survive both.
        await generate_weekly_pool(session)
        await session.flush()
        await generate_weekly_pool(session)
        await session.flush()

        refreshed = (await session.execute(
            select(Bounty).where(Bounty.bounty_id == "manual/preserved")
        )).scalar_one()
        assert refreshed.source == "manual"
        assert refreshed.status == "active"

    async def test_empty_tier_skipped_gracefully(self, session):
        # Seed only easy maps strictly inside C range (duel<1.7). B and A
        # should produce 0 bounties without raising.
        for i in range(15):
            duel = 0.2 + (i * 0.09)  # 0.20..1.46 — all strictly inside C
            session.add(DuelMapPool(
                beatmap_id=70_000 + i, beatmapset_id=80_000 + i,
                title=f"easy{i}", artist="x", version="d", creator="m",
                star_rating=duel * 2.0, bpm=180.0, length=200,
                ar=8.0, od=8.0, cs=4.0, hp_drain=5.0,
                w_aim=0.25, w_speed=0.25, w_acc=0.25, w_cons=0.25,
                aim_stars=duel, speed_stars=duel, acc_stars=duel, cons_stars=duel,
                map_type="aim", enabled=True,
            ))
        await session.flush()

        pool = await generate_weekly_pool(session)
        await session.flush()

        rows = (await session.execute(
            select(Bounty).where(Bounty.source == "auto")
        )).scalars().all()
        tiers = {b.tier for b in rows}
        # C and Open get all 9 (Open shares full range with C/B/A). A and B
        # should be missing entirely — no exceptions raised.
        assert "C" in tiers
        assert "Open" in tiers
        assert "A" not in tiers
        assert "B" not in tiers

    async def test_conditions_serialised_as_json(self, session):
        await _seed_maps(session)
        await generate_weekly_pool(session)
        await session.flush()

        # Any bounty with non-NULL conditions must round-trip through json.loads.
        with_conds = (await session.execute(
            select(Bounty).where(Bounty.conditions.is_not(None))
        )).scalars().all()
        assert len(with_conds) > 0  # at least some rules produce conditions
        for b in with_conds:
            data = json.loads(b.conditions)
            assert isinstance(data, dict)

    async def test_legacy_columns_mirrored(self, session):
        await _seed_maps(session)
        await generate_weekly_pool(session)
        await session.flush()

        # Accuracy/SS bounties → min_accuracy set on the legacy column.
        acc_bounties = (await session.execute(
            select(Bounty).where(Bounty.bounty_type.in_(["Accuracy", "SS"]))
        )).scalars().all()
        for b in acc_bounties:
            assert b.min_accuracy is not None
            # And the same value lives in the JSON blob.
            cond = json.loads(b.conditions)
            assert cond["min_accuracy"] == b.min_accuracy
