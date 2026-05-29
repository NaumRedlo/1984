"""Anti-farm multiplier for HP payouts.

Plan: unified-giggling-tiger (step 6/9).

Simplified 2026-05-29 per player feedback (Вованчик):
  - Removed the per-category "specialist" penalty entirely. Doing only
    Speed bounties is now free — bounties are fixed-payout orders,
    specialization isn't farming.
  - Removed the composite floor (was 0.3). Same-map repeats can now
    decay all the way to 0 if the user grinds the same beatmap.

What's left:
    same_map_factor = 0.7 ^ N
      where N = approved submissions this user has on the same
      beatmap_id (across all bounties). Re-doing the same map for
      different bounty types still counts as repetition.

Pure async function over an `AsyncSession`.  Returns (multiplier,
breakdown) so the caller can record the components in the payout
breakdown shown in the bot UI / dryrun script.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.bounty import Bounty, Submission


# ── Tunables ───────────────────────────────────────────────────────────────

# Per-repeat decay base.  0.7^N: 1st repeat 0.7, 2nd 0.49, 3rd 0.343, …
SAME_MAP_BASE = 0.7


async def compute_anti_farm_multiplier(
    session: AsyncSession,
    *,
    user_id: int,
    beatmap_id: int,
    bounty_type: str,
    now: Optional[datetime] = None,
) -> tuple[float, dict]:
    """Return (multiplier ∈ [0.0, 1.0], breakdown dict).

    Counts the user's approved submissions on `beatmap_id` (via JOIN on
    bounties). The query excludes the current submission being scored —
    anti_farm runs *before* the new row is approved.

    `bounty_type` is accepted for API stability but no longer used (the
    same-type penalty was removed). `now` defaults to datetime.utcnow();
    pass an explicit value for deterministic tests / dryrun replays.
    """
    if now is None:
        now = datetime.utcnow()

    # Same-map repeat count
    same_map_count = (await session.execute(
        select(func.count(Submission.id))
        .join(Bounty, Submission.bounty_id == Bounty.bounty_id)
        .where(
            Submission.user_id == user_id,
            Submission.status == "approved",
            Bounty.beatmap_id == beatmap_id,
        )
    )).scalar() or 0

    same_map_factor = SAME_MAP_BASE ** same_map_count
    composite = same_map_factor

    return composite, {
        "same_map_count": same_map_count,
        "same_map_factor": round(same_map_factor, 4),
        "composite": round(composite, 4),
    }


__all__ = ["compute_anti_farm_multiplier"]
