"""Integration tests for the HpsMapPool ⇄ weekly_generator switchover.

Plan: unified-giggling-tiger (step 8/9).

Covers:
  * generate_weekly_pool prefers HpsMapPool when it has rows.
  * last_used_at + use_count are marked on each pick.
  * Maps used inside the 28-day window are excluded from the next pool.
  * Falls back to BskMapPool when HpsMapPool is empty (covered by the
    pre-existing test_weekly_generator_integration suite).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.bounty import Bounty  # noqa: F401
from db.models.bsk_map_pool import BskMapPool  # noqa: F401
from db.models.hps_map_pool import HpsMapPool
from db.models.user import User  # noqa: F401
from db.models.weekly_bounty_pool import WeeklyBountyPool  # noqa: F401
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


async def _seed_hps_maps(session, count: int = 60) -> None:
    """Seed `count` HpsMapPool rows spanning SR 0.4..10.0 with varied tags.

    Same SR distribution as test_weekly_generator_integration._seed_maps so
    each tier ends up with ≥9 eligible maps. Each map carries a typing_hints
    JSON blob biased toward a different bounty type (round-robined).
    """
    bts = ["Accuracy", "SS", "Mod", "Pass", "Metronome", "First FC"]
    for i in range(count):
        bsk = 0.2 + (i * 4.8 / max(count - 1, 1))
        sr = bsk * 2.0
        # Boost one type per map so assign_bounty_type can pick the typed
        # variant when the SR-zone gate allows it.
        bt = bts[i % len(bts)]
        hints = {b: (0.8 if b == bt else 0.1) for b in (
            "Marathon", "SS", "Accuracy", "Metronome", "Mod", "Pass", "First FC"
        )}
        session.add(HpsMapPool(
            beatmap_id=10_000 + i,
            beatmapset_id=20_000 + i,
            title=f"song{i}", artist="artist", version="diff", creator="mapper",
            star_rating=sr, bpm=180.0,
            length=180 if i % 10 else 700,
            ar=9.0, od=9.0, cs=4.0,
            genre_tag="mixed", length_bucket="medium", bpm_bucket="mid",
            ranked_status="ranked",
            typing_hints=json.dumps(hints),
            enabled=True,
        ))
    await session.flush()


class TestHpsPoolPath:
    async def test_generator_uses_hps_pool_when_present(self, session):
        await _seed_hps_maps(session)
        pool = await generate_weekly_pool(session)
        await session.flush()
        rows = (await session.execute(
            select(Bounty).where(Bounty.source == "auto")
        )).scalars().all()
        # Bounty.beatmap_id must come from the HpsMapPool seed range.
        assert len(rows) == 36
        assert all(10_000 <= b.beatmap_id < 10_000 + 60 for b in rows)

    async def test_picks_stamp_last_used_at(self, session):
        await _seed_hps_maps(session)
        await generate_weekly_pool(session)
        await session.flush()

        # Every distinct beatmap_id that was picked must have last_used_at
        # set + use_count >= 1. Open tier overlaps C/B/A so the same map
        # may get picked twice — total stamped < 36 is fine, but every
        # bounty's beatmap must trace back to a stamped row.
        bounty_beatmaps = {b.beatmap_id for b in (await session.execute(
            select(Bounty).where(Bounty.source == "auto")
        )).scalars().all()}

        stamped = (await session.execute(
            select(HpsMapPool).where(HpsMapPool.last_used_at.is_not(None))
        )).scalars().all()
        stamped_ids = {m.beatmap_id for m in stamped}

        assert bounty_beatmaps == stamped_ids
        for m in stamped:
            assert m.use_count >= 1

    async def test_28_day_anti_repeat_excludes_recent(self, session):
        # Seed maps, hand-stamp half of them as "used 7 days ago".
        await _seed_hps_maps(session, count=60)
        recent_cutoff = _utcnow_naive() - timedelta(days=7)
        all_maps = (await session.execute(select(HpsMapPool))).scalars().all()
        recently_used_ids = {m.beatmap_id for m in all_maps[:30]}
        for m in all_maps[:30]:
            m.last_used_at = recent_cutoff
            m.use_count = 1
        await session.flush()

        await generate_weekly_pool(session)
        await session.flush()

        rows = (await session.execute(
            select(Bounty).where(Bounty.source == "auto")
        )).scalars().all()
        # No picked bounty should reference a map that was used 7 days ago.
        for b in rows:
            assert b.beatmap_id not in recently_used_ids, (
                f"map {b.beatmap_id} re-used inside the 28d window"
            )

    async def test_old_usage_does_not_block(self, session):
        # last_used_at older than 28 days → map IS eligible again.
        await _seed_hps_maps(session, count=60)
        old = _utcnow_naive() - timedelta(days=40)
        all_maps = (await session.execute(select(HpsMapPool))).scalars().all()
        for m in all_maps:
            m.last_used_at = old
        await session.flush()

        await generate_weekly_pool(session)
        await session.flush()
        rows = (await session.execute(
            select(Bounty).where(Bounty.source == "auto")
        )).scalars().all()
        # All 36 slots filled — none of the maps were locked out.
        assert len(rows) == 36
