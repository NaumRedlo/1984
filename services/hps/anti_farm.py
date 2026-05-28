"""Anti-farm multiplier for HP payouts.

Plan: unified-giggling-tiger (step 6/9).

Two independent penalties combine into F_repeat for `calculate_hps`:

  1. same_map_factor = 0.7 ^ N
     where N = approved submissions this user has on the same
     beatmap_id (across all bounties).  Re-doing the same map for
     different bounty types still counts as repetition.

  2. same_type_factor = 1.0 − 0.3 × max(0, r − 0.5)
     where r = fraction of the user's last-7-days approved submissions
     that share the current bounty_type.  Specialists who do nothing but
     `Speed` bounties get capped, generalists pay nothing.

The product is floored at 0.3 so a ten-times repeat doesn't collapse
HP to zero — the existing Ψ(Δ) skill penalty still does the heavy lift.

Pure async function over an `AsyncSession`.  Returns (multiplier,
breakdown) so the caller can record the components in the payout
breakdown shown in the bot UI / dryrun script.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.bounty import Bounty, Submission


# ── Tunables ───────────────────────────────────────────────────────────────

# Per-repeat decay base.  0.7^N: 1st repeat 0.7, 2nd 0.49, 3rd 0.343, …
SAME_MAP_BASE      = 0.7

# Window for the type-domination check.
SAME_TYPE_WINDOW_D = 7

# Below this ratio the type penalty is 0.  Above, each percentage point of
# share above the threshold subtracts SAME_TYPE_SLOPE × 0.01 from the factor.
SAME_TYPE_FLOOR    = 0.5
SAME_TYPE_SLOPE    = 0.3

# Hard floor on the composite — prevents zeroing out farmed-map payouts.
COMPOSITE_FLOOR    = 0.3


async def compute_anti_farm_multiplier(
    session: AsyncSession,
    *,
    user_id: int,
    beatmap_id: int,
    bounty_type: str,
    now: Optional[datetime] = None,
) -> tuple[float, dict]:
    """Return (multiplier ∈ [COMPOSITE_FLOOR, 1.0], breakdown dict).

    Counts the user's approved submissions on `beatmap_id` (via JOIN on
    bounties) and the share of `bounty_type` in their last-7-days
    approved submissions.  Both queries exclude the current submission
    being scored — anti_farm runs *before* the new row is approved.

    `now` defaults to datetime.utcnow(); pass an explicit value for
    deterministic tests / dryrun replays.
    """
    if now is None:
        now = datetime.utcnow()

    # ── 1. Same-map repeat count ────────────────────────────────────────
    same_map_count = (await session.execute(
        select(func.count(Submission.id))
        .join(Bounty, Submission.bounty_id == Bounty.bounty_id)
        .where(
            Submission.user_id   == user_id,
            Submission.status    == "approved",
            Bounty.beatmap_id    == beatmap_id,
        )
    )).scalar() or 0

    same_map_factor = SAME_MAP_BASE ** same_map_count

    # ── 2. Same-type ratio in last 7 days ───────────────────────────────
    window_start = now - timedelta(days=SAME_TYPE_WINDOW_D)

    rows = (await session.execute(
        select(Bounty.bounty_type, func.count(Submission.id))
        .join(Bounty, Submission.bounty_id == Bounty.bounty_id)
        .where(
            Submission.user_id     == user_id,
            Submission.status      == "approved",
            Submission.submitted_at >= window_start,
        )
        .group_by(Bounty.bounty_type)
    )).all()

    total_recent = sum(c for _, c in rows) or 0
    same_type    = sum(c for bt, c in rows if bt == bounty_type)
    same_type_ratio = (same_type / total_recent) if total_recent else 0.0

    excess = max(0.0, same_type_ratio - SAME_TYPE_FLOOR)
    same_type_factor = 1.0 - SAME_TYPE_SLOPE * excess
    # Defensive — never invert the multiplier even if tunables drift.
    same_type_factor = max(0.0, min(1.0, same_type_factor))

    # ── 3. Composite (floored) ─────────────────────────────────────────
    composite = max(COMPOSITE_FLOOR, same_map_factor * same_type_factor)

    return composite, {
        "same_map_count":    same_map_count,
        "same_map_factor":   round(same_map_factor, 4),
        "same_type_ratio_7d": round(same_type_ratio, 4),
        "same_type_factor":  round(same_type_factor, 4),
        "composite":         round(composite, 4),
    }


__all__ = ["compute_anti_farm_multiplier"]
