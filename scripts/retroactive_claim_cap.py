"""Retroactively apply the 3-auto-bounty-per-week claim cap.

Run once after introducing the weekly claim limit to fairly correct users who
submitted more than 3 auto-bounties in a single week before the rule existed.

Rule applied: per (user, week_id), keep HP only for the first 3 distinct
auto-bounties claimed (ranked by earliest submitted_at).  Submissions beyond
that get hp_awarded = 0.  Manual bounties (source='manual' or week_id=NULL)
are untouched.

After zeroing over-limit submissions: rebuilds User.hps_points = SUM(hp_awarded)
and re-derives User.rank.

Idempotent — running twice produces the same result.

Usage:
    python3 -m scripts.retroactive_claim_cap             # apply
    python3 -m scripts.retroactive_claim_cap --dry-run   # report only
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select, func

from db.database import engine, get_db_session
from db.migrations import run_all_migrations
from db.models.bounty import Bounty, Submission
from db.models.user import User
from utils.hp_calculator import get_rank_for_hp


WEEKLY_CLAIM_CAP = 6


async def _find_over_limit_submissions() -> dict[int, dict]:
    """
    Returns {sub_id: {"old_hp": int, "reason": str}} for every submission
    that should be zeroed under the retroactive cap.
    """
    to_zero: dict[int, dict] = {}

    async with get_db_session() as session:
        # All approved submissions for auto-bounties that belong to a week.
        rows = (await session.execute(
            select(
                Submission.id,
                Submission.user_id,
                Submission.bounty_id,
                Submission.hp_awarded,
                Submission.submitted_at,
                Bounty.week_id,
            )
            .join(Bounty, Bounty.bounty_id == Submission.bounty_id)
            .where(
                Submission.status == "approved",
                Bounty.source == "auto",
                Bounty.week_id.is_not(None),
            )
            .order_by(Submission.user_id, Bounty.week_id, Submission.submitted_at)
        )).all()

    # Group by (user_id, week_id) → list of rows in chronological order.
    by_user_week: dict[tuple, list] = defaultdict(list)
    for row in rows:
        by_user_week[(row.user_id, row.week_id)].append(row)

    for (user_id, week_id), week_rows in by_user_week.items():
        # Determine claim order: earliest submitted_at per distinct bounty.
        bounty_first: dict[str, float] = {}
        for row in week_rows:
            ts = row.submitted_at.timestamp() if row.submitted_at else 0.0
            if row.bounty_id not in bounty_first:
                bounty_first[row.bounty_id] = ts

        # First WEEKLY_CLAIM_CAP distinct bounties by claim date.
        allowed = {
            bid for bid, _ in sorted(bounty_first.items(), key=lambda x: x[1])[:WEEKLY_CLAIM_CAP]
        }

        for row in week_rows:
            if row.bounty_id not in allowed and (row.hp_awarded or 0) != 0:
                to_zero[row.id] = {
                    "old_hp": row.hp_awarded or 0,
                    "user_id": user_id,
                    "week_id": week_id,
                    "bounty_id": row.bounty_id,
                }

    return to_zero


async def run_cap(*, dry_run: bool) -> None:
    await run_all_migrations(engine)

    print()
    print("═" * 78)
    print(" Retroactive claim cap — finding over-limit submissions")
    print("═" * 78)

    to_zero = await _find_over_limit_submissions()

    if not to_zero:
        print("\nNo over-limit submissions found — nothing to do.")
        return

    # Group by user for reporting.
    by_user: dict[int, list] = defaultdict(list)
    for sid, info in to_zero.items():
        by_user[info["user_id"]].append((sid, info))

    total_hp_removed = sum(v["old_hp"] for v in to_zero.values())
    print(f"\nSubmissions to zero:  {len(to_zero)}")
    print(f"Affected users:       {len(by_user)}")
    print(f"Total HP to remove:   -{total_hp_removed}")
    print()

    async with get_db_session() as session:
        user_ids = list(by_user.keys())
        users_map = {
            u.id: u for u in (await session.execute(
                select(User).where(User.id.in_(user_ids))
            )).scalars().all()
        }

    print("Per-user breakdown:")
    for uid, entries in sorted(by_user.items()):
        uname = users_map.get(uid)
        uname_str = uname.osu_username if uname else f"id:{uid}"
        hp_lost = sum(e[1]["old_hp"] for e in entries)
        weeks = {e[1]["week_id"] for e in entries}
        print(f"  {uname_str:<24} -{hp_lost:>5} HP   "
              f"({len(entries)} submissions, weeks: {sorted(weeks)})")

    if dry_run:
        print()
        print("═" * 78)
        print(" DRY RUN — no changes committed.  Drop --dry-run to apply.")
        print("═" * 78)
        return

    print()
    print("═" * 78)
    print(" Applying cap")
    print("═" * 78)

    # Zero out over-limit submissions.
    async with get_db_session() as session:
        for sid in to_zero:
            sub = (await session.execute(
                select(Submission).where(Submission.id == sid)
            )).scalar_one_or_none()
            if sub:
                sub.hp_awarded = 0
        await session.commit()

    print(f"Zeroed {len(to_zero)} submissions.")

    # Rebuild user totals.
    async with get_db_session() as session:
        users = (await session.execute(
            select(User).where(User.id.in_(list(by_user.keys())))
        )).scalars().all()

        for u in users:
            total = (await session.execute(
                select(func.coalesce(func.sum(Submission.hp_awarded), 0))
                .where(Submission.user_id == u.id, Submission.status == "approved")
            )).scalar() or 0
            u.hps_points = int(total)
            u.rank = get_rank_for_hp(u.hps_points)

        await session.commit()

    print("User totals and ranks rebuilt.")
    print()
    print("═" * 78)
    print(" Done")
    print("═" * 78)


def main() -> None:
    parser = argparse.ArgumentParser(description="Retroactive weekly claim cap")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only, commit nothing.")
    args = parser.parse_args()
    asyncio.run(run_cap(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
