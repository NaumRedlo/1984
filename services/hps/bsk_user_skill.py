"""Per-axis BSK_user skill score for the HPS Ψ(Δ) module.

Each user has four scalar skill values in [0..10] — aim/speed/acc/cons — used
by the HPS payout to compute Δ = BSK_map − BSK_user.  The HPS Manifest defines
these as the **weighted average BSK-stars of the user's top-10 successful
submissions in the last 90 days**, per axis.

Implementation details agreed upon in design:
  * Bootstrap for thin histories: `BSK_user_pp = clamp((pp/1000)^0.6 + 3, 0, 10)`,
    blended into the submission-derived value with weight α = N/10, where N is
    the count of qualifying submissions within the 90-day window.
  * "Qualifying" = approved AND result_type ∈ {"win", "condition"}.
  * Per-submission weight = exp(−Δt_days / 30) × C_pen, where C_pen comes from
    the same formula HPS uses (combo and miss penalties).
  * Top-10 are selected per axis by (weight × axis_stars) so axes with weaker
    data don't pollute axes with strong data.
  * Maps absent from `bsk_map_pool` contribute via a fallback: all four axis
    stars set equal to the submission's bounty.star_rating.  This keeps the
    formula working for arbitrary admin-picked beatmaps.

This module is pure compute against an open session: the caller is responsible
for committing.  `refresh_bsk_user_skill` is the convenience wrapper that
writes the cached values back onto `User`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.bounty import Bounty, Submission
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User


AxisName = Literal["aim", "speed", "acc", "cons"]
AXES: tuple[AxisName, ...] = ("aim", "speed", "acc", "cons")

# Window for "recent successful submissions" the Manifest mentions.
WINDOW_DAYS = 90
# Time decay constant.  At Δt = 30 days a submission's weight is e^-1 ≈ 0.37.
TIME_DECAY_DAYS = 30.0
# Bootstrap mixing target: at this many qualifying submissions, α → 1.0 and
# the PP-derived prior is fully washed out.
BOOTSTRAP_FULL_N = 10
# Per-axis top-K used in the weighted average.
TOP_K = 10
# Neutral midpoint for users with no submissions and no PP (or no OAuth user).
NEUTRAL_DEFAULT = 4.0


def _bsk_user_pp_prior(player_pp: Optional[int]) -> float:
    """The PP-derived baseline applied uniformly to all axes for new users.

    Uses a power curve so top-PP players get a meaningfully higher start than
    log10 would allow (a 20k-PP player should not collapse to ~6.3 alongside
    a 10k-PP player).  Result is clamped to the [0..10] axis range.
    """
    if not player_pp or player_pp <= 0:
        return NEUTRAL_DEFAULT
    val = (player_pp / 1000.0) ** 0.6 + 3.0
    return max(0.0, min(10.0, val))


def _c_pen(combo: Optional[int], max_combo: Optional[int], misses: Optional[int]) -> float:
    """Replicates the HPS C_pen formula on submission data.

    Uses sqrt(combo / max_combo) × 0.92^misses, with NULL-tolerance: when the
    submission doesn't carry combo info we fall back to 1.0 for the combo
    factor (it cannot have been worse than a full pass since the submission
    was approved as win/condition) and apply only the miss penalty.
    """
    if combo is None or max_combo is None or max_combo <= 0:
        combo_factor = 1.0
    else:
        ratio = max(0.0, min(1.0, combo / max_combo))
        combo_factor = math.sqrt(ratio)
    miss_factor = 0.92 ** (int(misses) if misses else 0)
    return combo_factor * miss_factor


def _map_axis_stars(pool_row: Optional[BskMapPool], bounty: Bounty) -> dict[AxisName, float]:
    """Per-axis stars for a beatmap, with a SR-flat fallback.

    For maps registered in `bsk_map_pool` we use the independent `*_stars`
    fields directly.  When a star value is missing on a pooled row (older
    imports before the v2 pattern features) or the map is not in the pool,
    we substitute the bounty's overall star rating across all four axes.
    """
    fallback = float(bounty.star_rating or 0.0)
    if pool_row is None:
        return {axis: fallback for axis in AXES}
    return {
        "aim":   float(pool_row.aim_stars   if pool_row.aim_stars   is not None else fallback),
        "speed": float(pool_row.speed_stars if pool_row.speed_stars is not None else fallback),
        "acc":   float(pool_row.acc_stars   if pool_row.acc_stars   is not None else fallback),
        "cons":  float(pool_row.cons_stars  if pool_row.cons_stars  is not None else fallback),
    }


@dataclass(slots=True)
class BskUserSkill:
    aim: float
    speed: float
    acc: float
    cons: float
    alpha: float          # 0..1, fraction of submission-driven signal in the blend
    qualifying_count: int  # # of approved win/condition submissions in window
    pp_prior: float        # the bootstrap value, for diagnostics

    def as_dict(self) -> dict[str, float]:
        return {axis: getattr(self, axis) for axis in AXES}


async def compute_bsk_user_skill(
    user: User,
    session: AsyncSession,
    *,
    as_of: Optional[datetime] = None,
) -> BskUserSkill:
    """Calculate BSK_user without touching the User row.

    Pure compute path — the caller decides whether to persist (see
    `refresh_bsk_user_skill`).  Splitting this out also makes it usable from
    the dry-run / backfill scripts where we want to evaluate the function for
    a *historical* point in time without overwriting current state.

    `as_of` (UTC, naive) is the reference timestamp for the 90-day window and
    the time-decay weights.  Defaults to "now".  When set to the timestamp of
    a specific submission, the function excludes that submission and any later
    ones — useful for dry-run, where we want to know "what BSK_user was when
    this score landed", not "what it is today after that score and everything
    since".
    """
    pp_prior = _bsk_user_pp_prior(user.player_pp)

    ref_time = as_of if as_of is not None else datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = ref_time - timedelta(days=WINDOW_DAYS)

    rows = (await session.execute(
        select(Submission, Bounty)
        .join(Bounty, Bounty.bounty_id == Submission.bounty_id)
        .where(
            Submission.user_id == user.id,
            Submission.status == "approved",
            Submission.result_type.in_(("win", "condition")),
            Submission.submitted_at >= cutoff,
            Submission.submitted_at < ref_time,
        )
    )).all()

    qualifying_count = len(rows)
    if qualifying_count == 0:
        return BskUserSkill(
            aim=pp_prior, speed=pp_prior, acc=pp_prior, cons=pp_prior,
            alpha=0.0, qualifying_count=0, pp_prior=pp_prior,
        )

    beatmap_ids = {b.beatmap_id for _sub, b in rows if b.beatmap_id}
    pool_rows: dict[int, BskMapPool] = {}
    if beatmap_ids:
        pool_result = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id.in_(beatmap_ids))
        )).scalars().all()
        pool_rows = {row.beatmap_id: row for row in pool_result}

    weighted: dict[AxisName, list[tuple[float, float]]] = {axis: [] for axis in AXES}

    for sub, bounty in rows:
        submitted_at = sub.submitted_at or ref_time
        delta_days = max(0.0, (ref_time - submitted_at).total_seconds() / 86400.0)
        time_w = math.exp(-delta_days / TIME_DECAY_DAYS)

        c_pen = _c_pen(sub.max_combo, bounty.max_combo, sub.misses)
        weight = time_w * c_pen
        if weight <= 0.0:
            continue

        stars = _map_axis_stars(pool_rows.get(bounty.beatmap_id), bounty)
        for axis in AXES:
            weighted[axis].append((stars[axis], weight))

    subs_skill: dict[AxisName, float] = {}
    for axis in AXES:
        entries = weighted[axis]
        if not entries:
            subs_skill[axis] = pp_prior
            continue
        entries.sort(key=lambda sw: sw[0] * sw[1], reverse=True)
        top = entries[:TOP_K]
        total_w = sum(w for _, w in top)
        if total_w <= 0.0:
            subs_skill[axis] = pp_prior
        else:
            subs_skill[axis] = sum(s * w for s, w in top) / total_w

    alpha = min(1.0, qualifying_count / BOOTSTRAP_FULL_N)
    blended: dict[AxisName, float] = {
        axis: max(0.0, min(10.0, (1.0 - alpha) * pp_prior + alpha * subs_skill[axis]))
        for axis in AXES
    }

    return BskUserSkill(
        aim=blended["aim"],
        speed=blended["speed"],
        acc=blended["acc"],
        cons=blended["cons"],
        alpha=alpha,
        qualifying_count=qualifying_count,
        pp_prior=pp_prior,
    )


async def refresh_bsk_user_skill(user: User, session: AsyncSession) -> BskUserSkill:
    """Compute and write BSK_user back to the user row.

    Caller still needs to commit.  Updates `bsk_skill_calculated_at` so we can
    detect stale rows in a periodic refresher.
    """
    skill = await compute_bsk_user_skill(user, session)
    user.bsk_user_aim   = skill.aim
    user.bsk_user_speed = skill.speed
    user.bsk_user_acc   = skill.acc
    user.bsk_user_cons  = skill.cons
    user.bsk_skill_calculated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return skill
