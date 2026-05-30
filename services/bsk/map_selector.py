"""
BSK map selector — picks a map from bsk_map_pool based on target star rating.
Adaptive pressure: winner gets +0.3★, anti-snowball if score gap > 30%.
"""

import random
from typing import Optional

from sqlalchemy import select, func, case
from db.database import get_db_session
from db.models.bsk_map_pool import BskMapPool
from utils.logger import get_logger

logger = get_logger("bsk.pool")


def _bsk_map_expr():
    """SQLAlchemy expression for BSK_map = Σ w_i · stars_i.

    NULL weights fall back to 0.25 (equal split); NULL per-axis stars fall back
    to the overall osu! star_rating stored on the row.
    """
    sr = BskMapPool.star_rating
    w_aim   = func.coalesce(BskMapPool.w_aim,        0.25)
    w_spd   = func.coalesce(BskMapPool.w_speed,      0.25)
    w_acc   = func.coalesce(BskMapPool.w_acc,        0.25)
    w_cons  = func.coalesce(BskMapPool.w_cons,       0.25)
    s_aim   = func.coalesce(BskMapPool.aim_stars,   sr)
    s_spd   = func.coalesce(BskMapPool.speed_stars, sr)
    s_acc   = func.coalesce(BskMapPool.acc_stars,   sr)
    s_cons  = func.coalesce(BskMapPool.cons_stars,  sr)
    return w_aim * s_aim + w_spd * s_spd + w_acc * s_acc + w_cons * s_cons

MIN_MAP_LENGTH = 105


def _length_filter():
    return BskMapPool.length >= MIN_MAP_LENGTH


def _summarize_picks(maps: list[BskMapPool]) -> str:
    """One-line compact summary of a candidate list — id/SR/BSK/type."""
    if not maps:
        return "[]"
    parts = []
    for m in maps:
        # Inline-compute BSK from the row's own columns (we already have them).
        w_aim   = m.w_aim   if m.w_aim   is not None else 0.25
        w_spd   = m.w_speed if m.w_speed is not None else 0.25
        w_acc   = m.w_acc   if m.w_acc   is not None else 0.25
        w_cons  = m.w_cons  if m.w_cons  is not None else 0.25
        sr      = m.star_rating or 0.0
        s_aim   = m.aim_stars   if m.aim_stars   is not None else sr
        s_spd   = m.speed_stars if m.speed_stars is not None else sr
        s_acc   = m.acc_stars   if m.acc_stars   is not None else sr
        s_cons  = m.cons_stars  if m.cons_stars  is not None else sr
        bsk     = w_aim*s_aim + w_spd*s_spd + w_acc*s_acc + w_cons*s_cons
        parts.append(f"{m.beatmap_id}({sr:.1f}★/BSK{bsk:.1f}/{m.map_type or '∅'})")
    return "[" + ", ".join(parts) + "]"


async def log_pool_health() -> dict:
    """Snapshot the BSK pool state and write a one-line summary to logs.

    Call once at startup (or on demand from an admin command) so the
    operator can immediately see whether a pool is unhealthy:
      - too few enabled maps overall,
      - many rows missing per-axis stars (rendering BSK ≈ SR for them),
      - many rows missing map_type (breaks get_balanced_pick_candidates),
      - skewed map_type distribution (e.g. all 'mixed').

    Returns the same numbers in a dict so callers can also surface them.
    """
    async with get_db_session() as session:
        # Use SUM(CASE …) rather than multiple COUNTs so it's one query.
        row = (await session.execute(select(
            func.count(BskMapPool.beatmap_id).label("total"),
            func.sum(case((BskMapPool.enabled == True, 1), else_=0)).label("enabled"),
            func.sum(case((BskMapPool.aim_stars.is_(None), 1), else_=0)).label("missing_axis"),
            func.sum(case((BskMapPool.map_type.is_(None), 1), else_=0)).label("missing_type"),
            func.sum(case((BskMapPool.length.is_(None), 1), else_=0)).label("missing_length"),
        ))).one()
        type_rows = (await session.execute(
            select(BskMapPool.map_type, func.count(BskMapPool.beatmap_id))
            .where(BskMapPool.enabled == True)
            .group_by(BskMapPool.map_type)
        )).all()

    total = int(row.total or 0)
    enabled = int(row.enabled or 0)
    missing_axis = int(row.missing_axis or 0)
    missing_type = int(row.missing_type or 0)
    missing_length = int(row.missing_length or 0)
    type_dist = {(t or "∅"): int(c) for t, c in type_rows}

    summary = {
        "total": total, "enabled": enabled,
        "missing_axis_stars": missing_axis,
        "missing_map_type":   missing_type,
        "missing_length":     missing_length,
        "type_distribution":  type_dist,
    }

    # Tag emergencies plainly so they pop in greps.
    flags: list[str] = []
    if enabled < 30:
        flags.append("THIN_POOL")
    if total and missing_axis / max(total, 1) > 0.3:
        flags.append("STARS_MISSING")
    if total and missing_type / max(total, 1) > 0.3:
        flags.append("TYPES_MISSING")
    components = {"aim", "speed", "acc", "cons", "mixed"}
    represented = {t for t in type_dist.keys() if t in components}
    if components - represented:
        flags.append(f"MISSING_COMPONENTS={','.join(sorted(components - represented))}")

    flag_str = (" flags=" + ",".join(flags)) if flags else ""
    logger.info(
        f"pool_health: total={total} enabled={enabled} "
        f"missing_axis={missing_axis} missing_type={missing_type} "
        f"missing_length={missing_length} types={type_dist}{flag_str}"
    )
    return summary


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
        ("easier", target_sr - 1.0, target_sr - 0.3, base + (1 if rem > 0 else 0)),
        ("on-par", target_sr - 0.3, target_sr + 0.3, base + (1 if rem > 1 else 0)),
        ("harder", target_sr + 0.3, target_sr + 1.0, base),
    ]

    chosen: list[BskMapPool] = []
    leftover_slots = 0
    diag_bands: list[str] = []

    async with get_db_session() as session:
        def _base_stmt():
            stmt = select(BskMapPool).where(
                BskMapPool.enabled == True,
                _length_filter(),
            )
            if exclude_ids:
                stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))
            return stmt

        bsk = _bsk_map_expr()
        for name, lo, hi, slots in bands:
            wanted = slots + leftover_slots
            leftover_slots = 0
            pool = (await session.execute(
                _base_stmt().where(
                    bsk >= lo,
                    bsk <= hi,
                )
            )).scalars().all()
            # Exclude maps already chosen in previous bands
            chosen_ids = {m.beatmap_id for m in chosen}
            pool = [m for m in pool if m.beatmap_id not in chosen_ids]
            picked_here = 0
            if len(pool) >= wanted:
                taken = random.sample(pool, wanted)
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
                _base_stmt().where(BskMapPool.beatmap_id.notin_(chosen_ids))
            )).scalars().all()
            taken = random.sample(rest, min(leftover_slots, len(rest)))
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
    # Diagnostic accumulators — one line per component, then a summary.
    component_diag: list[str] = []

    async with get_db_session() as session:
        def _stmt():
            stmt = select(BskMapPool).where(
                BskMapPool.enabled == True,
                _length_filter(),
            )
            if exclude:
                stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude))
            return stmt

        bsk = _bsk_map_expr()

        # ── 1. One map per component, widening SR window if needed ──
        deltas = (sr_window, sr_window + 0.5, sr_window + 1.0, sr_window + 1.5)
        for component in ("aim", "speed", "acc", "cons", "mixed"):
            picked = None
            tried: list[str] = []
            for delta in deltas:
                rows = (await session.execute(
                    _stmt().where(
                        BskMapPool.map_type == component,
                        bsk >= target_sr - delta,
                        bsk <= target_sr + delta,
                    )
                )).scalars().all()
                rows = [m for m in rows if m.beatmap_id not in chosen_ids]
                tried.append(f"Δ{delta:.1f}={len(rows)}")
                if rows:
                    picked = random.choice(rows)
                    break
            if picked:
                chosen.append(picked)
                chosen_ids.add(picked.beatmap_id)
                component_diag.append(
                    f"{component}:✓({picked.beatmap_id} BSK?,{','.join(tried)})"
                )
            else:
                component_diag.append(f"{component}:✗({','.join(tried)})")

        # ── 2. Random fillers, plus refill any missed component slots ──
        slots_needed = 5 + fillers - len(chosen)
        filler_diag = ""
        if slots_needed > 0:
            tried_fill: list[str] = []
            for delta in (sr_window, sr_window + 0.5, sr_window + 1.0, sr_window + 1.5, 99.0):
                rows = (await session.execute(
                    _stmt().where(
                        bsk >= target_sr - delta,
                        bsk <= target_sr + delta,
                        BskMapPool.beatmap_id.notin_(list(chosen_ids) or [0]),
                    )
                )).scalars().all()
                tried_fill.append(f"Δ{delta:.1f}={len(rows)}")
                if len(rows) >= slots_needed:
                    chosen.extend(random.sample(rows, slots_needed))
                    filler_diag = f" fillers:✓({slots_needed} need,{','.join(tried_fill)})"
                    break
                elif rows and delta >= 99.0:
                    chosen.extend(rows[:slots_needed])
                    filler_diag = (
                        f" fillers:⚠last-resort({len(rows)}/{slots_needed} need,"
                        f"{','.join(tried_fill)})"
                    )
                    break
            else:
                filler_diag = f" fillers:✗({slots_needed} need,{','.join(tried_fill)})"

    logger.info(
        f"get_balanced_pick_candidates: target_sr={target_sr:.2f} "
        f"window={sr_window} excludes={len(exclude)} | "
        f"{' '.join(component_diag)}{filler_diag} "
        f"final={len(chosen)} → {_summarize_picks(chosen)}"
    )

    random.shuffle(chosen)
    return chosen


async def get_map_for_round(
    target_sr: float,
    exclude_ids: list[int] | None = None,
    sr_delta: float = 0.5,
) -> Optional[BskMapPool]:
    """Pick a random enabled map, gradually widening the SR window."""
    bsk = _bsk_map_expr()
    deltas_tried: list[str] = []
    chosen_via = "unset"
    picked: Optional[BskMapPool] = None
    async with get_db_session() as session:
        for delta in [sr_delta, 1.0, 1.5, 2.0]:
            stmt = select(BskMapPool).where(
                BskMapPool.enabled == True,
                _length_filter(),
                bsk >= target_sr - delta,
                bsk <= target_sr + delta,
            )
            if exclude_ids:
                stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))
            maps = (await session.execute(stmt)).scalars().all()
            deltas_tried.append(f"Δ{delta:.1f}={len(maps)}")
            if maps:
                picked = random.choice(maps)
                chosen_via = f"window(Δ{delta:.1f})"
                break
        else:
            # Last resort: any enabled map ignoring SR.
            stmt = select(BskMapPool).where(
                BskMapPool.enabled == True,
                _length_filter(),
            )
            if exclude_ids:
                stmt = stmt.where(BskMapPool.beatmap_id.notin_(exclude_ids))
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
            logger.info(
                f"next_star_rating: anti-snowball reset to base_sr={base_sr:.2f} "
                f"(gap={gap:.0%} > {SR_GAP_RESET_THRESHOLD:.0%}, "
                f"p1={p1_total:.0f} p2={p2_total:.0f})"
            )
            return base_sr

    leader = 1 if p1_total > p2_total else 2 if p2_total > p1_total else None
    if round_winner == leader:
        candidate = current_sr + SR_PRESSURE_STEP
        reason = "leader-won"
    else:
        candidate = current_sr
        reason = "trailer-won" if leader else "tied"

    capped = min(candidate, base_sr + SR_CAP_OFFSET)
    final = round(capped, 1)
    logger.info(
        f"next_star_rating: {current_sr:.2f} → {final:.2f} "
        f"(reason={reason}, winner=p{round_winner}, "
        f"p1={p1_total:.0f} p2={p2_total:.0f}, base={base_sr:.2f}, "
        f"cap={base_sr + SR_CAP_OFFSET:.2f})"
    )
    return final
