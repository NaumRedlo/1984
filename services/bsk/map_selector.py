"""
BSK map selector — picks a map from bsk_map_pool based on target star rating.
Adaptive pressure: winner gets +0.3★, anti-snowball if score gap > 30%.
"""

import random
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.bsk_map_pool import BskMapPool


async def get_map_for_round(
    target_sr: float,
    exclude_ids: list[int] | None = None,
    sr_delta: float = 0.5,
) -> Optional[BskMapPool]:
    """Pick a random enabled map within target_sr ± sr_delta, excluding already played maps."""
    async with get_db_session() as session:
        stmt = select(BskMapPool).where(
            BskMapPool.enabled == True,
            BskMapPool.star_rating >= target_sr - sr_delta,
            BskMapPool.star_rating <= target_sr + sr_delta,
        )
        if exclude_ids:
            stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))

        maps = (await session.execute(stmt)).scalars().all()
        if not maps:
            # Widen search if nothing found
            stmt2 = select(BskMapPool).where(BskMapPool.enabled == True)
            if exclude_ids:
                stmt2 = stmt2.where(BskMapPool.beatmap_id.notin_(exclude_ids))
            maps = (await session.execute(stmt2)).scalars().all()

        if not maps:
            return None

        chosen = random.choice(maps)
        # Detach from session by reading all fields
        return chosen


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
