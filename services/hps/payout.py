"""High-level HPS v2 payout helper.

Glues the moving parts together for the rest of the bot:
    bounty + submission + user + DB session
        ↓
    MapInfo (via duel_map_pool lookup, SR fallback otherwise)
    PlayerSkill (via services.hps.duel_user_skill, with bootstrap)
    ScoreStats (from raw counts + mods)
        ↓
    utils.hp_calculator.calculate_hps  →  dict breakdown

Used by `bounty_auto_checker._check_once`, the admin review handler, and the
backfill script.  Keeps the formula's call signature in one place so future
tuning (Base, Vanguard) only needs to touch hp_calculator.
"""

from __future__ import annotations

from datetime import datetime
from utils.timeutils import utcnow
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.bounty import Bounty, Submission
from db.models.duel_map_pool import DuelMapPool
from db.models.hps_map_pool import HpsMapPool
from db.models.user import User
from services.hps.anti_farm import compute_anti_farm_multiplier
from services.hps.duel_user_skill import compute_duel_user_skill
from utils.hp_calculator import (
    MapInfo,
    PlayerSkill,
    ScoreStats,
    calculate_hps,
)
async def _map_info_for_bounty(bounty: Bounty, session: AsyncSession) -> tuple[MapInfo, bool]:
    """Build MapInfo for a bounty.

    Lookup order (plan: unified-giggling-tiger, step 8):
      1. duel_map_pool — best: per-axis ML stars + weights.
      2. hps_map_pool — partial: SR-only stars, default 0.25 weights
         (HPS pool doesn't carry per-axis ML calibration).
      3. SR fallback   — last resort: all axes = bounty.star_rating.

    The two-pool fallback means manual bounties on maps that haven't
    been DUEL-ingested still get a meaningful HpsMapPool reading for
    drain_time/max_combo if the HPS generator picked them.

    Returns (map_info, used_fallback) — `used_fallback` stays True only
    when neither pool has the map.
    """
    if not bounty.beatmap_id:
        return MapInfo.fallback_from_sr(
            star_rating=float(bounty.star_rating or 0.0),
            od=float(bounty.od or 0.0),
            drain_time=int(bounty.drain_time or 0),
            max_combo=int(bounty.max_combo or 0),
        ), True

    duel = (await session.execute(
        select(DuelMapPool).where(DuelMapPool.beatmap_id == bounty.beatmap_id)
    )).scalar_one_or_none()

    if duel is not None:
        # The per-axis classifier was removed — objective star_rating is the
        # single difficulty signal, so all four axes share it with flat 0.25
        # weights (the dormant aim/speed/acc/cons_stars columns are ignored).
        sr = float(duel.star_rating or bounty.star_rating or 0.0)
        return MapInfo(
            aim_stars=sr, speed_stars=sr, acc_stars=sr, cons_stars=sr,
            w_aim=0.25, w_speed=0.25, w_acc=0.25, w_cons=0.25,
            od=float(bounty.od or duel.od or 0.0),
            drain_time_seconds=int(bounty.drain_time or duel.length or 0),
            max_combo=int(bounty.max_combo or duel.max_combo or 0),
        ), False

    hps = (await session.execute(
        select(HpsMapPool).where(HpsMapPool.beatmap_id == bounty.beatmap_id)
    )).scalar_one_or_none()

    if hps is not None:
        sr = float(hps.star_rating or bounty.star_rating or 0.0)
        return MapInfo(
            aim_stars=sr, speed_stars=sr, acc_stars=sr, cons_stars=sr,
            w_aim=0.25, w_speed=0.25, w_acc=0.25, w_cons=0.25,
            od=float(bounty.od or hps.od or 0.0),
            drain_time_seconds=int(bounty.drain_time or hps.length or 0),
            max_combo=int(bounty.max_combo or hps.max_combo or 0),
        ), False

    return MapInfo.fallback_from_sr(
        star_rating=float(bounty.star_rating or 0.0),
        od=float(bounty.od or 0.0),
        drain_time=int(bounty.drain_time or 0),
        max_combo=int(bounty.max_combo or 0),
    ), True


def compute_score_ur(*, stored_ur: Optional[float] = None, **_kwargs) -> Optional[float]:
    """Return real UR for a submission when available.

    Only stored (replay-parsed) UR is accepted. Estimation has been removed;
    None → Ω=1.0 neutral in calculate_hps.
    """
    return float(stored_ur) if stored_ur is not None else None


async def compute_payout(
    *,
    session: AsyncSession,
    user: User,
    bounty: Bounty,
    submission: Submission,
    result_type: str,
    is_first_submission: bool,
    as_of: Optional[datetime] = None,
    bounty_type: Optional[str] = None,
) -> dict:
    """Run the full v2 payout for a submission.

    `as_of` controls DUEL_user reconstruction:
      * None → use today's data (live auto_checker / admin review).
      * a datetime → use only submissions strictly before this moment (backfill
        and dry-run reproduce history honestly).

    Returns the breakdown dict from `calculate_hps`, augmented with a
    `"used_fallback_map": bool` flag for downstream logging.
    """
    map_info, used_fallback = await _map_info_for_bounty(bounty, session)

    skill = await compute_duel_user_skill(user, session, as_of=as_of)
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
        stored_ur=float(submission.ur_est) if submission.ur_est is not None else None,
    )

    # ── Anti-farm + bootstrap multipliers (plan: unified-giggling-tiger). ──
    # Both run against the *current* submission state; the submission row may
    # not yet be marked approved, so the queries naturally exclude it.
    effective_bt = bounty_type or bounty.bounty_type
    af_mult, af_breakdown = await compute_anti_farm_multiplier(
        session,
        user_id=user.id,
        beatmap_id=int(bounty.beatmap_id or 0),
        bounty_type=effective_bt or "",
        now=as_of,
    )

    days_since: Optional[int] = None
    if user.first_approved_at is not None:
        ref = as_of or utcnow()
        days_since = max(0, (ref - user.first_approved_at).days)

    result = calculate_hps(
        result_type=result_type,
        map_info=map_info,
        player_skill=player_skill,
        score=score,
        is_first_submission=is_first_submission,
        ur_est_override=ur_override,
        bounty_type=bounty_type,
        anti_farm_multiplier=af_mult,
        days_since_first_approved=days_since,
    )
    result["used_fallback_map"] = used_fallback
    result["anti_farm_breakdown"] = af_breakdown
    return result


__all__ = ["compute_payout", "compute_score_ur"]
