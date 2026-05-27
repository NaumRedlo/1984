"""Recalculate bounty submission payouts after the 2026-05-27 fix.

Bugs fixed (commit context):
  1. `required_mods` was checked with `issubset` instead of strict equality —
     extra difficulty-altering mods (DT, EZ, HR, …) silently passed.
  2. NM bounties (required_mods=NULL) accepted ANY mods.
  3. Auto-generated bounties had `max_combo=0` so the `c_pen` combo factor
     was always 1.0, ignoring partial-fail submissions.

This script walks every approved submission, reconstructs its `_check_conditions`
verdict using the NEW strict logic against the stored mods/acc/miss, and:
  * If the verdict is now `pending` → the submission must NOT have been
    auto-approved. Mark it `rejected_recalc`, zero `hp_awarded`, refund the
    delta to User.hps_points.
  * If it still passes → leave the row alone. Payout recompute (cap, max_combo)
    is out of scope for this script; deal with it in a follow-up if anyone
    is still above 500 per submission.

Default mode is dry-run. Pass `--apply` to commit.

Usage:
    python3 -m scripts.recalc_bounty_payouts            # dry-run, prints diff
    python3 -m scripts.recalc_bounty_payouts --apply    # commits to DB
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from db.models.user import User
from tasks.bounty_auto_checker import (
    _check_conditions,
    _parse_required_mods,
    HARMLESS_MODS,
)
from utils.hp_calculator import get_rank_for_hp


def _mods_set_from_str(raw: str | None) -> set[str]:
    """Mirror _extract_mods but read from Submission.mods (stored as string)."""
    if not raw:
        return set()
    parts = {m.strip().upper() for m in raw.replace(",", " ").split() if m.strip()}
    return parts - HARMLESS_MODS


def _synthetic_score(sub: Submission) -> dict:
    """Re-build the minimum score dict that `_check_conditions` needs from
    the columns we persisted at approval time."""
    return {
        "accuracy": (sub.accuracy or 0) / 100.0,
        "max_combo": int(sub.max_combo or 0),
        "statistics": {"count_miss": int(sub.misses or 0)},
        # Stored as comma-separated string of acronyms.
        "mods": [m for m in (sub.mods or "").replace(",", " ").split() if m],
    }


async def main(apply: bool) -> None:
    refunds: dict[int, int] = defaultdict(int)   # user_id -> total HP refunded
    affected_rows: list[tuple[Submission, Bounty, str]] = []

    async with get_db_session() as session:
        subs = (await session.execute(
            select(Submission).where(Submission.status == "approved")
        )).scalars().all()

        if not subs:
            print("No approved submissions to review — nothing to do.")
            return

        bounty_ids = list({s.bounty_id for s in subs})
        bounties = (await session.execute(
            select(Bounty).where(Bounty.bounty_id.in_(bounty_ids))
        )).scalars().all()
        bounty_map = {b.bounty_id: b for b in bounties}

        for sub in subs:
            b = bounty_map.get(sub.bounty_id)
            if not b:
                continue

            score = _synthetic_score(sub)
            # ur_est=stored value when present; conservative for max_ur checks.
            ur_est = float(sub.ur_est) if sub.ur_est is not None else None
            # beatmap_max_combo=bounty.max_combo (may be 0 — _check_conditions
            # treats 0 as "unknown" and fails min_combo_pct safely).
            verdict, _ok = _check_conditions(
                score, b, ur_est=ur_est, beatmap_max_combo=int(b.max_combo or 0),
            )

            if verdict == "pending":
                # Was approved under the old buggy check — refund.
                hp = int(sub.hp_awarded or 0)
                refunds[sub.user_id] += hp
                affected_rows.append((sub, b, _diff_reason(sub, b)))

        # Print report.
        print(f"\n══════ RECALC REPORT ({'APPLY' if apply else 'DRY-RUN'}) ══════")
        print(f"Approved submissions reviewed: {len(subs)}")
        print(f"Now-failing submissions:       {len(affected_rows)}")
        print(f"Affected users:                {len(refunds)}\n")

        for sub, b, reason in affected_rows:
            print(
                f"  sub_id={sub.id:>4} user_id={sub.user_id:>3} "
                f"bounty={b.bounty_id} type={b.bounty_type} "
                f"hp={sub.hp_awarded} mods={sub.mods!r} req={b.required_mods!r} "
                f"acc={sub.accuracy} miss={sub.misses} | {reason}"
            )

        if refunds:
            print("\nUser refunds:")
            users = (await session.execute(
                select(User).where(User.id.in_(refunds.keys()))
            )).scalars().all()
            user_map = {u.id: u for u in users}
            for uid, hp in sorted(refunds.items(), key=lambda x: -x[1]):
                u = user_map.get(uid)
                name = u.osu_username if u else f"<uid={uid}>"
                current = u.hps_points if u else 0
                new_hp = max(0, current - hp)
                new_rank = get_rank_for_hp(new_hp)
                print(
                    f"  {name:<24} -{hp:>5} HP  "
                    f"({current} → {new_hp}, rank: {new_rank})"
                )

        if not apply:
            print("\n(dry-run — no changes committed; re-run with --apply)")
            return

        # ─── APPLY ─────────────────────────────────────────────────────────
        # 1. Zero hp_awarded and re-status the failing submissions.
        for sub, _b, _reason in affected_rows:
            sub.status = "rejected_recalc"
            sub.hp_awarded = 0

        # 2. Refund the delta to each user's hps_points, recompute rank.
        users = (await session.execute(
            select(User).where(User.id.in_(refunds.keys()))
        )).scalars().all()
        for u in users:
            u.hps_points = max(0, (u.hps_points or 0) - refunds.get(u.id, 0))
            u.rank = get_rank_for_hp(u.hps_points)

        await session.commit()
        print("\n✓ Committed: submissions rejected, hps_points refunded.")


def _diff_reason(sub: Submission, b: Bounty) -> str:
    """Why does the new check fail this row? — for the operator's report."""
    reasons = []
    played = _mods_set_from_str(sub.mods)
    required = _parse_required_mods(b.required_mods)
    if played != required:
        reasons.append(f"mods {played or '∅'} ≠ required {required or '∅'}")
    if b.min_accuracy and (sub.accuracy or 0) < float(b.min_accuracy):
        reasons.append(f"acc {sub.accuracy} < {b.min_accuracy}")
    if b.max_misses is not None and int(sub.misses or 0) > int(b.max_misses):
        reasons.append(f"miss {sub.misses} > {b.max_misses}")
    return "; ".join(reasons) or "(other condition)"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="commit changes; default is dry-run")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
