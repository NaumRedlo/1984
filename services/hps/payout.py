"""High-level HPS v2 payout helper.

Glues the moving parts together for the rest of the bot:
    bounty + submission + user + DB session
        ↓
    MapInfo (via bsk_map_pool lookup, SR fallback otherwise)
    PlayerSkill (via services.hps.bsk_user_skill, with bootstrap)
    ScoreStats (from raw counts + mods)
        ↓
    utils.hp_calculator.calculate_hps_v2  →  dict breakdown

Used by `bounty_auto_checker._check_once`, the admin review handler, and the
backfill script.  Keeps the formula's call signature in one place so future
tuning (Base, Vanguard) only needs to touch hp_calculator.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.bounty import Bounty, Submission
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User
from services.hps.bsk_user_skill import compute_bsk_user_skill
from utils.hp_calculator import (
    MapInfo,
    PlayerSkill,
    ScoreStats,
    calculate_hps_v2,
)
from utils.osu.ur_estimator import estimate_ur


async def _map_info_for_bounty(bounty: Bounty, session: AsyncSession) -> tuple[MapInfo, bool]:
    """Build MapInfo for a bounty, preferring bsk_map_pool data over SR fallback.

    Returns (map_info, used_fallback).
    """
    if not bounty.beatmap_id:
        return MapInfo.fallback_from_sr(
            star_rating=float(bounty.star_rating or 0.0),
            od=float(bounty.od or 0.0),
            drain_time=int(bounty.drain_time or 0),
            max_combo=int(bounty.max_combo or 0),
        ), True

    pool = (await session.execute(
        select(BskMapPool).where(BskMapPool.beatmap_id == bounty.beatmap_id)
    )).scalar_one_or_none()

    if pool is None:
        return MapInfo.fallback_from_sr(
            star_rating=float(bounty.star_rating or 0.0),
            od=float(bounty.od or 0.0),
            drain_time=int(bounty.drain_time or 0),
            max_combo=int(bounty.max_combo or 0),
        ), True

    sr = float(bounty.star_rating or 0.0)
    return MapInfo(
        aim_stars=float(pool.aim_stars   if pool.aim_stars   is not None else sr),
        speed_stars=float(pool.speed_stars if pool.speed_stars is not None else sr),
        acc_stars=float(pool.acc_stars   if pool.acc_stars   is not None else sr),
        cons_stars=float(pool.cons_stars  if pool.cons_stars  is not None else sr),
        w_aim=float(pool.w_aim   if pool.w_aim   is not None else 0.25),
        w_speed=float(pool.w_speed if pool.w_speed is not None else 0.25),
        w_acc=float(pool.w_acc   if pool.w_acc   is not None else 0.25),
        w_cons=float(pool.w_cons if pool.w_cons is not None else 0.25),
        od=float(bounty.od or pool.od or 0.0),
        drain_time_seconds=int(bounty.drain_time or pool.length or 0),
        max_combo=int(bounty.max_combo or 0),
    ), False


def compute_score_ur(
    *,
    n_300: int,
    n_100: int,
    n_50: int,
    od: float,
    mods,
    stored_ur: Optional[float] = None,
) -> Optional[float]:
    """Return UR for a submission, preferring a stored value over recomputation.

    `stored_ur` lets callers pass `submission.ur_est` directly: when present we
    use it as-is (auto_checker has already done the math at score time), saving
    the Hastings cycle.  Falls through to live estimation otherwise.
    """
    if stored_ur is not None:
        return float(stored_ur)
    return estimate_ur(int(n_300 or 0), int(n_100 or 0), int(n_50 or 0), od=od, mods=mods)


async def compute_payout(
    *,
    session: AsyncSession,
    user: User,
    bounty: Bounty,
    submission: Submission,
    result_type: str,
    is_first_submission: bool,
    as_of: Optional[datetime] = None,
) -> dict:
    """Run the full v2 payout for a submission.

    `as_of` controls BSK_user reconstruction:
      * None → use today's data (live auto_checker / admin review).
      * a datetime → use only submissions strictly before this moment (backfill
        and dry-run reproduce history honestly).

    Returns the breakdown dict from `calculate_hps_v2`, augmented with a
    `"used_fallback_map": bool` flag for downstream logging.
    """
    map_info, used_fallback = await _map_info_for_bounty(bounty, session)

    skill = await compute_bsk_user_skill(user, session, as_of=as_of)
    player_skill = PlayerSkill(
        aim=skill.aim, speed=skill.speed, acc=skill.acc, cons=skill.cons,
    )

    score = ScoreStats(
        n_300=int(submission.n_300 or 0),
        n_100=int(submission.n_100 or 0),
        n_50=int(submission.n_50 or 0),
        misses=int(submission.misses or 0),
        combo=int(submission.max_combo or 0),
        mods=submission.mods,
    )

    ur_override = compute_score_ur(
        n_300=submission.n_300 or 0,
        n_100=submission.n_100 or 0,
        n_50=submission.n_50 or 0,
        od=map_info.od,
        mods=submission.mods,
        stored_ur=float(submission.ur_est) if submission.ur_est is not None else None,
    )

    result = calculate_hps_v2(
        result_type=result_type,
        map_info=map_info,
        player_skill=player_skill,
        score=score,
        is_first_submission=is_first_submission,
        ur_est_override=ur_override,
    )
    result["used_fallback_map"] = used_fallback
    return result


__all__ = ["compute_payout", "compute_score_ur"]
