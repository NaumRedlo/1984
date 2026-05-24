"""
BSK map selector — picks a map from bsk_map_pool based on target star rating.
Adaptive pressure: winner gets +0.3★, anti-snowball if score gap > 30%.
"""

import random
from typing import Optional

from sqlalchemy import or_, select
from db.database import get_db_session
from db.models.bsk_map_pool import BskMapPool

MIN_MAP_LENGTH = 105


async def get_pick_candidates(
    target_sr: float,
    n: int = 6,
    exclude_ids: list[int] | None = None,
) -> list[BskMapPool]:
    """
    Return `n` maps for the pick phase spread across three difficulty bands:
      easier  — [target_sr - 1.0 .. target_sr - 0.3)   → n // 3 slots
      on-par  — [target_sr - 0.3 .. target_sr + 0.3]   → n // 3 slots
      harder  — (target_sr + 0.3 .. target_sr + 1.0]   → remaining slots

    If a band doesn't have enough maps, its slots are redistributed to the
    other bands. Falls back to a flat random sample if the pool is tiny.
    """
    base = n // 3
    rem  = n % 3

    bands = [
        (target_sr - 1.0, target_sr - 0.3, base + (1 if rem > 0 else 0)),
        (target_sr - 0.3, target_sr + 0.3, base + (1 if rem > 1 else 0)),
        (target_sr + 0.3, target_sr + 1.0, base),
    ]

    chosen: list[BskMapPool] = []
    leftover_slots = 0

    async with get_db_session() as session:
        def _base_stmt():
            stmt = select(BskMapPool).where(
                BskMapPool.enabled == True,
                or_(BskMapPool.length >= MIN_MAP_LENGTH, BskMapPool.length.is_(None)),
            )
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


async def get_balanced_pick_candidates(
    target_sr: float,
    exclude_ids: list[int] | None = None,
    sr_window: float = 1.0,
    fillers: int = 1,
) -> list[BskMapPool]:
    """
    Build a 6-map pool with guaranteed component coverage:
      - 1 map per skill component (aim, speed, acc, cons, mixed) — by `map_type`
      - +`fillers` random maps from the SR window (default 1 → total 6)

    If a component has no maps in the SR window, the slot is dropped and
    refilled later from any-component fillers (so the pool never shrinks
    below the requested size when the broader pool has enough maps).

    All maps are unique. SR window starts narrow and widens until enough
    maps are found.
    """
    exclude = set(exclude_ids or [])
    chosen: list[BskMapPool] = []
    chosen_ids: set[int] = set()

    async with get_db_session() as session:
        def _stmt():
            stmt = select(BskMapPool).where(
                BskMapPool.enabled == True,
                or_(BskMapPool.length >= MIN_MAP_LENGTH, BskMapPool.length.is_(None)),
            )
            if exclude:
                stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude))
            return stmt

        # ── 1. One map per component, widening SR window if needed ──
        for component in ("aim", "speed", "acc", "cons", "mixed"):
            picked = None
            for delta in (sr_window, sr_window + 0.5, sr_window + 1.0, sr_window + 1.5):
                rows = (await session.execute(
                    _stmt().where(
                        BskMapPool.map_type == component,
                        BskMapPool.star_rating >= target_sr - delta,
                        BskMapPool.star_rating <= target_sr + delta,
                    )
                )).scalars().all()
                rows = [m for m in rows if m.beatmap_id not in chosen_ids]
                if rows:
                    picked = random.choice(rows)
                    break
            if picked:
                chosen.append(picked)
                chosen_ids.add(picked.beatmap_id)

        # ── 2. Random fillers, plus refill any missed component slots ──
        slots_needed = 5 + fillers - len(chosen)
        if slots_needed > 0:
            for delta in (sr_window, sr_window + 0.5, sr_window + 1.0, sr_window + 1.5, 99.0):
                rows = (await session.execute(
                    _stmt().where(
                        BskMapPool.star_rating >= target_sr - delta,
                        BskMapPool.star_rating <= target_sr + delta,
                        BskMapPool.beatmap_id.notin_(list(chosen_ids) or [0]),
                    )
                )).scalars().all()
                if len(rows) >= slots_needed:
                    chosen.extend(random.sample(rows, slots_needed))
                    break
                elif rows and delta >= 99.0:
                    chosen.extend(rows[:slots_needed])
                    break

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
                or_(BskMapPool.length >= MIN_MAP_LENGTH, BskMapPool.length.is_(None)),
                BskMapPool.star_rating >= target_sr - delta,
                BskMapPool.star_rating <= target_sr + delta,
            )
            if exclude_ids:
                stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))
            maps = (await session.execute(stmt)).scalars().all()
            if maps:
                return random.choice(maps)

        stmt = select(BskMapPool).where(
            BskMapPool.enabled == True,
            or_(BskMapPool.length >= MIN_MAP_LENGTH, BskMapPool.length.is_(None)),
        )
        if exclude_ids:
            stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))
        maps = (await session.execute(stmt)).scalars().all()
        return random.choice(maps) if maps else None


SR_PRESSURE_STEP = 0.3
SR_GAP_RESET_THRESHOLD = 0.50
SR_CAP_OFFSET = 1.5


def next_star_rating(
    current_sr: float,
    round_winner: int,          # 1 or 2
    p1_total: float,
    p2_total: float,
    base_sr: float,
) -> float:
    """Adaptive star-rating pressure for the next round.

    Rules:
      • Anti-snowball — if the cumulative score gap exceeds
        ``SR_GAP_RESET_THRESHOLD`` (50%) of the combined total, reset to
        ``base_sr``. Avoids burying a player who's already far behind.
      • Pressure step — if the round winner is also currently leading by total
        score, raise SR by ``SR_PRESSURE_STEP`` (★0.3). If a trailing player
        wins the round, SR stays put — they get a chance to catch up at the
        same difficulty.
      • Cap — SR is clamped to ``base_sr + SR_CAP_OFFSET`` (★1.5) so the pool
        window in :func:`get_balanced_pick_candidates` always contains maps.
    """
    total = p1_total + p2_total
    if total > 0:
        gap = abs(p1_total - p2_total) / total
        if gap > SR_GAP_RESET_THRESHOLD:
            return base_sr

    leader = 1 if p1_total > p2_total else 2 if p2_total > p1_total else None
    if round_winner == leader:
        candidate = current_sr + SR_PRESSURE_STEP
    else:
        candidate = current_sr

    capped = min(candidate, base_sr + SR_CAP_OFFSET)
    return round(capped, 1)
