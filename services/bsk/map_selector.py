"""
BSK map selector — picks a map from bsk_map_pool based on target star rating.
Adaptive pressure: winner gets +0.3★, anti-snowball if score gap > 30%.
"""

import random
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.bsk_map_pool import BskMapPool


async def get_pick_candidates(
    target_sr: float,
    n: int = 6,
    exclude_ids: list[int] | None = None,
) -> list[BskMapPool]:
    """
    Return `n` maps for the pick phase spread across three difficulty bands:
      easier  — [target_sr - 1.5 .. target_sr - 0.5)   → n // 3 slots
      on-par  — [target_sr - 0.5 .. target_sr + 0.5]   → n // 3 slots
      harder  — (target_sr + 0.5 .. target_sr + 1.5]   → remaining slots

    If a band doesn't have enough maps, its slots are redistributed to the
    other bands. Falls back to a flat random sample if the pool is tiny.
    """
    per_band = n // 3          # 2 for n=6
    extra    = n - per_band * 3  # 0 for n=6

    bands = [
        (target_sr - 1.5, target_sr - 0.5, per_band),
        (target_sr - 0.5, target_sr + 0.5, per_band),
        (target_sr + 0.5, target_sr + 1.5, per_band + extra),
    ]

    chosen: list[BskMapPool] = []
    leftover_slots = 0

    async with get_db_session() as session:
        def _base_stmt():
            stmt = select(BskMapPool).where(BskMapPool.enabled == True)
            if exclude_ids:
                stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))
            return stmt

        for lo, hi, slots in bands:
            slots += leftover_slots
            leftover_slots = 0
            pool = (await session.execute(
                _base_stmt().where(
                    BskMapPool.star_rating >= lo,
                    BskMapPool.star_rating <= hi,
                )
            )).scalars().all()
            # Exclude maps already chosen in previous bands
            chosen_ids = {m.beatmap_id for m in chosen}
            pool = [m for m in pool if m.beatmap_id not in chosen_ids]
            if len(pool) >= slots:
                chosen.extend(random.sample(pool, slots))
            else:
                chosen.extend(pool)
                leftover_slots += slots - len(pool)

        # Fill any remaining slots from the whole pool
        if leftover_slots > 0:
            chosen_ids = {m.beatmap_id for m in chosen}
            rest = (await session.execute(
                _base_stmt().where(BskMapPool.beatmap_id.notin_(chosen_ids))
            )).scalars().all()
            chosen.extend(random.sample(rest, min(leftover_slots, len(rest))))

    random.shuffle(chosen)
    return chosen


async def get_map_for_round(
    target_sr: float,
    exclude_ids: list[int] | None = None,
    sr_delta: float = 0.5,
) -> Optional[BskMapPool]:
    """Pick a random enabled map, gradually widening the SR window."""
    async with get_db_session() as session:
        for delta in [sr_delta, 1.0, 1.5, 2.0]:
            stmt = select(BskMapPool).where(
                BskMapPool.enabled == True,
                BskMapPool.star_rating >= target_sr - delta,
                BskMapPool.star_rating <= target_sr + delta,
            )
            if exclude_ids:
                stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))
            maps = (await session.execute(stmt)).scalars().all()
            if maps:
                return random.choice(maps)

        stmt = select(BskMapPool).where(BskMapPool.enabled == True)
        if exclude_ids:
            stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))
        maps = (await session.execute(stmt)).scalars().all()
        return random.choice(maps) if maps else None


def next_star_rating(
    current_sr: float,
    round_winner: int,          # 1 or 2
    p1_total: float,
    p2_total: float,
    base_sr: float,
) -> float:
    """
    Adaptive pressure:
    - Winner of round gets +0.3★ pressure next round
    - If score gap > 30% of total — reset toward base_sr (anti-snowball)
    """
    total = p1_total + p2_total
    if total > 0:
        gap = abs(p1_total - p2_total) / total
        if gap > 0.30:
            return base_sr  # anti-snowball

    return round(current_sr + 0.3, 1)
