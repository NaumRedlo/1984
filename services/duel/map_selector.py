"""
DUEL map selector — builds a duel pool from duel_map_pool around a target SR
(the average of both players' ratings). The pick set is spread across three SR
bands (easier / on-par / harder) and, within each band, across a BPM/length
fingerprint so the six maps don't all share one play-style. The main pool is
static; `get_map_for_round` is the per-round fallback used only for tiebreakers.
"""

import random
from typing import Optional

from sqlalchemy import select, func, case
from db.database import get_db_session
from db.models.duel_map_pool import DuelMapPool
from utils.logger import get_logger

logger = get_logger("duel.pool")


def _duel_map_expr():
    """Difficulty signal for map selection — the objective osu! star_rating.

    (The old per-axis skill classifier was removed; star rating is the single
    accurate difficulty measure.)
    """
    return DuelMapPool.star_rating

MIN_MAP_LENGTH = 105


def _length_filter():
    return DuelMapPool.length >= MIN_MAP_LENGTH


def _summarize_picks(maps: list[DuelMapPool]) -> str:
    """One-line compact summary of a candidate list — id/SR."""
    if not maps:
        return "[]"
    parts = [f"{m.beatmap_id}({(m.star_rating or 0.0):.1f}★)" for m in maps]
    return "[" + ", ".join(parts) + "]"


def _spread_key(m: DuelMapPool) -> tuple[float, int]:
    """Play-style fingerprint used to spread a pick set: BPM, then length.

    Since the per-skill classifier was removed, BPM is the cheapest objective
    proxy for 'what kind of map is this' (streams vs. slow aim), and length
    separates sprints from marathons. Maps adjacent on this key feel similar.
    """
    return (float(m.bpm or 0.0), int(m.length or 0))


def _spread_sample(pool: list[DuelMapPool], k: int) -> list[DuelMapPool]:
    """Pick `k` maps from `pool` spread across the BPM/length fingerprint.

    Sort by `_spread_key`, cut into `k` contiguous quantile buckets, and take a
    random map from each. This keeps the draw random while avoiding a pick set
    that clusters on one play-style (e.g. six near-identical-BPM stream maps).
    Falls back to a plain shuffle when the pool can't fill `k`.
    """
    if k <= 0:
        return []
    if len(pool) <= k:
        out = list(pool)
        random.shuffle(out)
        return out
    ordered = sorted(pool, key=_spread_key)
    n = len(ordered)
    chosen: list[DuelMapPool] = []
    for i in range(k):
        lo = (i * n) // k
        hi = ((i + 1) * n) // k
        bucket = ordered[lo:hi] or ordered[lo:lo + 1]
        chosen.append(random.choice(bucket))
    return chosen


async def log_pool_health() -> dict:
    """Snapshot the DUEL pool state and write a one-line summary to logs.

    Call once at startup (or on demand from an admin command) so the operator
    can see whether the pool is unhealthy (too few enabled maps, or rows
    missing a length so `_length_filter` drops them).
    """
    async with get_db_session() as session:
        row = (await session.execute(select(
            func.count(DuelMapPool.beatmap_id).label("total"),
            func.sum(case((DuelMapPool.enabled == True, 1), else_=0)).label("enabled"),
            func.sum(case((DuelMapPool.length.is_(None), 1), else_=0)).label("missing_length"),
        ))).one()

    total = int(row.total or 0)
    enabled = int(row.enabled or 0)
    missing_length = int(row.missing_length or 0)

    summary = {"total": total, "enabled": enabled, "missing_length": missing_length}

    flags: list[str] = []
    if enabled < 30:
        flags.append("THIN_POOL")
    flag_str = (" flags=" + ",".join(flags)) if flags else ""
    logger.info(
        f"pool_health: total={total} enabled={enabled} "
        f"missing_length={missing_length}{flag_str}"
    )
    return summary


async def get_pick_candidates(
    target_sr: float,
    n: int = 6,
    exclude_ids: list[int] | None = None,
) -> list[DuelMapPool]:
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
        ("easier", target_sr - 1.0, target_sr - 0.3, base + (1 if rem > 0 else 0)),
        ("on-par", target_sr - 0.3, target_sr + 0.3, base + (1 if rem > 1 else 0)),
        ("harder", target_sr + 0.3, target_sr + 1.0, base),
    ]

    chosen: list[DuelMapPool] = []
    leftover_slots = 0
    diag_bands: list[str] = []

    async with get_db_session() as session:
        def _base_stmt():
            stmt = select(DuelMapPool).where(
                DuelMapPool.enabled == True,
                _length_filter(),
            )
            if exclude_ids:
                stmt = stmt.where(DuelMapPool.beatmap_id.notin_(exclude_ids))
            return stmt

        duel = _duel_map_expr()
        for name, lo, hi, slots in bands:
            wanted = slots + leftover_slots
            leftover_slots = 0
            pool = (await session.execute(
                _base_stmt().where(
                    duel >= lo,
                    duel <= hi,
                )
            )).scalars().all()
            # Exclude maps already chosen in previous bands
            chosen_ids = {m.beatmap_id for m in chosen}
            pool = [m for m in pool if m.beatmap_id not in chosen_ids]
            picked_here = 0
            if len(pool) >= wanted:
                taken = _spread_sample(pool, wanted)
                chosen.extend(taken)
                picked_here = len(taken)
            else:
                chosen.extend(pool)
                picked_here = len(pool)
                leftover_slots += wanted - len(pool)
            diag_bands.append(
                f"{name}[{lo:.1f}..{hi:.1f}]={len(pool)}avail/{picked_here}picked"
            )

        # Fill any remaining slots from the whole pool
        overflow_picked = 0
        if leftover_slots > 0:
            chosen_ids = {m.beatmap_id for m in chosen}
            rest = (await session.execute(
                _base_stmt().where(DuelMapPool.beatmap_id.notin_(chosen_ids))
            )).scalars().all()
            taken = _spread_sample(rest, min(leftover_slots, len(rest)))
            chosen.extend(taken)
            overflow_picked = len(taken)

    overflow_str = (
        f" overflow_fill={overflow_picked}/{leftover_slots}"
        if leftover_slots else ""
    )
    logger.info(
        f"get_pick_candidates: target_sr={target_sr:.2f} n={n} "
        f"excludes={len(exclude_ids or [])} | "
        f"{' | '.join(diag_bands)}{overflow_str} "
        f"final={len(chosen)} → {_summarize_picks(chosen)}"
    )

    random.shuffle(chosen)
    return chosen


async def get_map_for_round(
    target_sr: float,
    exclude_ids: list[int] | None = None,
    sr_delta: float = 0.5,
) -> Optional[DuelMapPool]:
    """Pick a random enabled map, gradually widening the SR window."""
    duel = _duel_map_expr()
    deltas_tried: list[str] = []
    chosen_via = "unset"
    picked: Optional[DuelMapPool] = None
    async with get_db_session() as session:
        for delta in [sr_delta, 1.0, 1.5, 2.0]:
            stmt = select(DuelMapPool).where(
                DuelMapPool.enabled == True,
                _length_filter(),
                duel >= target_sr - delta,
                duel <= target_sr + delta,
            )
            if exclude_ids:
                stmt = stmt.where(DuelMapPool.beatmap_id.notin_(exclude_ids))
            maps = (await session.execute(stmt)).scalars().all()
            deltas_tried.append(f"Δ{delta:.1f}={len(maps)}")
            if maps:
                picked = random.choice(maps)
                chosen_via = f"window(Δ{delta:.1f})"
                break
        else:
            # Last resort: any enabled map ignoring SR.
            stmt = select(DuelMapPool).where(
                DuelMapPool.enabled == True,
                _length_filter(),
            )
            if exclude_ids:
                stmt = stmt.where(DuelMapPool.beatmap_id.notin_(exclude_ids))
            maps = (await session.execute(stmt)).scalars().all()
            deltas_tried.append(f"any={len(maps)}")
            if maps:
                picked = random.choice(maps)
                chosen_via = "fallback-any"

    logger.info(
        f"get_map_for_round: target_sr={target_sr:.2f} "
        f"excludes={len(exclude_ids or [])} | {','.join(deltas_tried)} | "
        f"via={chosen_via} → {_summarize_picks([picked] if picked else [])}"
    )
    return picked
