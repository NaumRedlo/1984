"""Backfill all approved submissions and users with v2 HPS payouts.

Run **after** dryrun_hps_recalc and confirming the report looks reasonable.

What it does, in one pass:
  1. Walks every approved submission in chronological order.
  2. For each, reconstructs BSK_user *as of the submission timestamp* — same
     honesty contract as the dry-run.
  3. Calls compute_payout → updates `submission.hp_awarded`.
  4. (Optional, --recompute-ur) re-runs the UR estimator over stored
     n_300/n_100/n_50 and writes the result to `submission.ur_est`.  Off by
     default because most legacy rows still have NULL hit counts.
  5. After the walk: rebuilds `User.hps_points = SUM(hp_awarded)`,
     re-derives `User.rank` against the v2 thresholds, and refreshes
     `User.bsk_user_*` to today's values.

Idempotent — running twice produces the same result.  Commits per batch
(default 100 submissions) so the database stays readable while running.

Usage:
    python3 -m scripts.backfill_hps             # default
    python3 -m scripts.backfill_hps --dry-run   # print plan, write nothing
    python3 -m scripts.backfill_hps --batch 50  # smaller batches
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from typing import Optional

from sqlalchemy import select, func

from db.database import engine, get_db_session
from db.migrations import run_all_migrations
from db.models.bounty import Bounty, Submission
from db.models.user import User
from services.hps.bsk_user_skill import refresh_bsk_user_skill
from services.hps.payout import compute_payout
from utils.hp_calculator import get_rank_for_hp_v2
from utils.osu.ur_estimator import estimate_ur


def _print_header(text: str) -> None:
    print()
    print("═" * 78)
    print(f" {text}")
    print("═" * 78)


async def _backfill_submissions(*, dry_run: bool, batch: int) -> dict:
    """Pass 1: rewrite hp_awarded for every approved submission."""
    stats = {
        "processed": 0,
        "changed": 0,
        "unchanged": 0,
        "fallback_map": 0,
        "missing_ur_inputs": 0,
        "delta_sum": 0,
    }

    # Track first-approved-per-bounty so Vanguard credit lands on the right row.
    first_seen: set[str] = set()

    async with get_db_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(Submission).where(Submission.status == "approved")
        )).scalar() or 0
        print(f"Approved submissions to process: {total}")
        if total == 0:
            return stats

    offset = 0
    while True:
        async with get_db_session() as session:
            page = (await session.execute(
                select(Submission)
                .where(Submission.status == "approved")
                .order_by(Submission.submitted_at.asc(), Submission.id.asc())
                .offset(offset).limit(batch)
            )).scalars().all()
            if not page:
                break

            # Bulk-fetch bounties and users for this page.
            bounty_ids = {s.bounty_id for s in page}
            bounties_map = {
                b.bounty_id: b for b in (await session.execute(
                    select(Bounty).where(Bounty.bounty_id.in_(bounty_ids))
                )).scalars().all()
            }
            user_ids = {s.user_id for s in page}
            users_map = {
                u.id: u for u in (await session.execute(
                    select(User).where(User.id.in_(user_ids))
                )).scalars().all()
            }

            for sub in page:
                stats["processed"] += 1

                bounty = bounties_map.get(sub.bounty_id)
                user = users_map.get(sub.user_id)
                if not bounty or not user:
                    continue  # orphaned — leave as-is

                is_first = sub.bounty_id not in first_seen
                first_seen.add(sub.bounty_id)

                if not (sub.n_300 or sub.n_100 or sub.n_50) and sub.ur_est is None:
                    stats["missing_ur_inputs"] += 1

                hp = await compute_payout(
                    session=session,
                    user=user,
                    bounty=bounty,
                    submission=sub,
                    result_type=sub.result_type or "participation",
                    is_first_submission=is_first,
                    as_of=sub.submitted_at,
                )
                if hp.get("used_fallback_map"):
                    stats["fallback_map"] += 1

                new_hp = hp["final_hp"]
                old_hp = int(sub.hp_awarded or 0)
                delta = new_hp - old_hp
                stats["delta_sum"] += delta

                if delta == 0:
                    stats["unchanged"] += 1
                else:
                    stats["changed"] += 1
                    if not dry_run:
                        sub.hp_awarded = new_hp

            if not dry_run:
                await session.commit()

        offset += len(page)
        print(f"  processed {offset}/{total}…")

    return stats


async def _resync_users(*, dry_run: bool) -> dict:
    """Pass 2: rebuild hps_points/rank/bsk_user_* from the now-backfilled rows."""
    stats = {
        "users": 0,
        "rank_changes": 0,
        "rank_transitions": defaultdict(int),
    }

    async with get_db_session() as session:
        users = (await session.execute(select(User))).scalars().all()
        for u in users:
            stats["users"] += 1
            total = (await session.execute(
                select(func.coalesce(func.sum(Submission.hp_awarded), 0))
                .where(Submission.user_id == u.id, Submission.status == "approved")
            )).scalar() or 0

            old_rank = u.rank
            new_rank = get_rank_for_hp_v2(total)
            if old_rank != new_rank:
                stats["rank_changes"] += 1
                stats["rank_transitions"][f"{old_rank} → {new_rank}"] += 1

            if dry_run:
                continue

            u.hps_points = int(total)
            u.rank = new_rank
            await refresh_bsk_user_skill(u, session)

        if not dry_run:
            await session.commit()

    return stats


async def run_backfill(*, dry_run: bool, batch: int) -> None:
    await run_all_migrations(engine)

    _print_header("Pass 1 — recompute submission HP")
    s1 = await _backfill_submissions(dry_run=dry_run, batch=batch)
    print()
    print(f"  processed:           {s1['processed']}")
    print(f"  values changed:      {s1['changed']}")
    print(f"  values unchanged:    {s1['unchanged']}")
    print(f"  map-pool fallback:   {s1['fallback_map']}")
    print(f"  missing UR inputs:   {s1['missing_ur_inputs']}")
    print(f"  net HP delta:        {s1['delta_sum']:+d}")

    _print_header("Pass 2 — resync User totals, ranks, and BSK_user")
    s2 = await _resync_users(dry_run=dry_run)
    print()
    print(f"  users touched:       {s2['users']}")
    print(f"  rank changes:        {s2['rank_changes']}")
    for trans, count in sorted(s2["rank_transitions"].items()):
        print(f"    {trans}: {count}")

    _print_header("Done")
    if dry_run:
        print("DRY RUN — no changes were committed.  Drop --dry-run to apply.")
    else:
        print("Backfill complete.  Verify a few user profiles before announcing.")


def main() -> None:
    parser = argparse.ArgumentParser(description="HPS v2 backfill")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute everything but never commit.")
    parser.add_argument("--batch", type=int, default=100,
                        help="Submissions per transaction (default 100).")
    args = parser.parse_args()
    asyncio.run(run_backfill(dry_run=args.dry_run, batch=args.batch))


if __name__ == "__main__":
    main()
