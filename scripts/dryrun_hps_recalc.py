"""Dry-run pass: recompute every approved submission's HP under the current formula.

Read-only — never commits.  Used to calibrate `HPS_BASE` and `HPS_VANGUARD`
against the existing dataset before the real backfill (#29).

What it does, per submission:
  1. Resolve the bounty + map info (bsk_map_pool lookup, SR fallback otherwise).
  2. Reconstruct the user's BSK_user *as of the submission timestamp* via
     `compute_bsk_user_skill(as_of=submission.submitted_at)` — this is the
     honest historical value, not today's.
  3. Use stored UR_est if present; if missing, Ω = 1.0.
  4. Call `calculate_hps` and record the new payout next to the old one.

What it reports:
  * Per-user totals: old hps_points, new hps_points, rank transitions.
  * Distribution of single-submission HP under v2 (median, p25/p50/p75/max)
    — this is the knob for tuning Base.
  * Vanguard frequency and average extra HP — knob for tuning vanguard_hp.
  * Coverage stats: how many submissions lack UR data, how many maps fell
    back to SR (not in bsk_map_pool).

Run from the project root:
    python3 -m scripts.dryrun_hps_recalc > report.txt
"""

from __future__ import annotations

import asyncio
import statistics
from collections import defaultdict
from typing import Iterable, Optional

from sqlalchemy import select

from db.database import engine, get_db_session
from db.migrations import run_all_migrations
from db.models.bounty import Bounty, Submission
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User
from services.hps.bsk_user_skill import compute_bsk_user_skill
from utils.hp_calculator import (
    MapInfo,
    PlayerSkill,
    RESULT_TYPE_MULTIPLIER,
    ScoreStats,
    calculate_hps,
    get_rank_for_hp,
)

# v1 thresholds kept here only for the rank-transition display column.
_V1_RANK_THRESHOLDS = [
    (4500, "Big Brother"),
    (2000, "Commissioner"),
    (900,  "Inspector"),
    (300,  "Member"),
    (0,    "Candidate"),
]

def _get_rank_v1(hp: int) -> str:
    for threshold, rank_name in _V1_RANK_THRESHOLDS:
        if hp >= threshold:
            return rank_name
    return "Candidate"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_map_info(bounty: Bounty, pool: Optional[BskMapPool]) -> tuple[MapInfo, bool]:
    """Returns (map_info, used_fallback)."""
    if pool is not None:
        sr = bounty.star_rating or 0.0
        # Older pool rows may carry NULL weights or NULL stars from the days
        # before BskMapPool had defaults; treat each NULL as "use SR" / "use
        # the equal-weight share" so the formula doesn't crash on real data.
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
    return MapInfo.fallback_from_sr(
        star_rating=float(bounty.star_rating or 0.0),
        od=float(bounty.od or 0.0),
        drain_time=int(bounty.drain_time or 0),
        max_combo=int(bounty.max_combo or 0),
    ), True


def _pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "—"
    return f"{100.0 * numerator / denominator:.1f}%"


def _quantile(data: list[int | float], q: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[idx]


def _print_header(text: str) -> None:
    print()
    print("═" * 78)
    print(f" {text}")
    print("═" * 78)


# ── Main ─────────────────────────────────────────────────────────────────────

async def run_dryrun() -> None:
    # Make sure the on-disk schema matches the ORM models — the script may be
    # the first thing to touch this database since v2 columns were added.
    # Migrations are idempotent (PRAGMA table_info guard).
    await run_all_migrations(engine)

    coverage_no_ur = 0
    coverage_fallback_map = 0
    total_subs = 0

    # Aggregations
    per_user_old: dict[int, int] = defaultdict(int)
    per_user_new: dict[int, int] = defaultdict(int)
    user_names: dict[int, str] = {}
    user_legacy_hp: dict[int, int] = {}

    payout_samples: list[int] = []   # final_hp distribution under v2
    vanguard_payouts: list[int] = []  # # of submissions awarded vanguard

    # ── Step 1: pull everything we need in one read-only window ─────────────
    async with get_db_session() as session:
        users = (await session.execute(select(User))).scalars().all()
        for u in users:
            user_names[u.id] = u.osu_username
            user_legacy_hp[u.id] = int(u.hps_points or 0)

        # Approved submissions sorted by submitted_at — the dry-run reproduces
        # the timeline so historical BSK_user computations build up correctly.
        all_subs = (await session.execute(
            select(Submission)
            .where(Submission.status == "approved")
            .order_by(Submission.submitted_at.asc())
        )).scalars().all()

        bounty_ids = {s.bounty_id for s in all_subs}
        bounties = (await session.execute(
            select(Bounty).where(Bounty.bounty_id.in_(bounty_ids))
        )).scalars().all()
        bounties_by_id = {b.bounty_id: b for b in bounties}

        beatmap_ids = {b.beatmap_id for b in bounties if b.beatmap_id}
        pool_rows = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id.in_(beatmap_ids))
        )).scalars().all()
        pool_by_id: dict[int, BskMapPool] = {p.beatmap_id: p for p in pool_rows}

        # Track "is this submission the first approved for this bounty" so we
        # can credit Vanguard the same way #30 will at write time.
        first_seen_bounty: set[str] = set()

        # ── Step 2: walk submissions, recompute v2 HP ───────────────────────
        for sub in all_subs:
            bounty = bounties_by_id.get(sub.bounty_id)
            if not bounty:
                # Orphaned submission — ignored just like the live checker would.
                continue

            user = next((u for u in users if u.id == sub.user_id), None)
            if not user:
                continue

            total_subs += 1

            pool = pool_by_id.get(bounty.beatmap_id) if bounty.beatmap_id else None
            map_info, used_fallback = _build_map_info(bounty, pool)
            if used_fallback:
                coverage_fallback_map += 1

            # Historical BSK_user at the moment of this submission.
            skill = await compute_bsk_user_skill(user, session, as_of=sub.submitted_at)
            player_skill = PlayerSkill(
                aim=skill.aim, speed=skill.speed, acc=skill.acc, cons=skill.cons,
            )

            score = ScoreStats(
                n_300=int(sub.n_300 or 0),
                n_100=int(sub.n_100 or 0),
                n_50=int(sub.n_50 or 0),
                misses=int(sub.misses or 0),
                combo=int(sub.max_combo or 0),
                mods=sub.mods,
            )
            # If neither stored UR nor any hit counts → UR remains None.
            ur_override = float(sub.ur_est) if sub.ur_est is not None else None
            if ur_override is None and not (sub.n_300 or sub.n_100 or sub.n_50):
                coverage_no_ur += 1

            is_first = sub.bounty_id not in first_seen_bounty
            first_seen_bounty.add(sub.bounty_id)

            result = calculate_hps(
                result_type=sub.result_type or "participation",
                map_info=map_info,
                player_skill=player_skill,
                score=score,
                is_first_submission=is_first,
                ur_est_override=ur_override,
            )

            new_hp = result["final_hp"]
            old_hp = int(sub.hp_awarded or 0)
            per_user_old[sub.user_id] += old_hp
            per_user_new[sub.user_id] += new_hp
            payout_samples.append(new_hp)
            if is_first:
                vanguard_payouts.append(result["vanguard"])

    # ── Step 3: report ──────────────────────────────────────────────────────
    _print_header("Dry-run summary — HPS v2 recompute")
    print(f"Submissions processed:      {total_subs}")
    print(f"  no UR data available:     {coverage_no_ur} ({_pct(coverage_no_ur, total_subs)})")
    print(f"  map-pool fallback (SR):   {coverage_fallback_map} ({_pct(coverage_fallback_map, total_subs)})")
    print(f"Users touched:              {len(per_user_new)}")

    _print_header("Single-submission HP distribution (v2)")
    if payout_samples:
        print(f"  min:      {min(payout_samples)}")
        print(f"  p25:      {_quantile(payout_samples, 0.25):.0f}")
        print(f"  median:   {statistics.median(payout_samples):.0f}")
        print(f"  p75:      {_quantile(payout_samples, 0.75):.0f}")
        print(f"  p95:      {_quantile(payout_samples, 0.95):.0f}")
        print(f"  max:      {max(payout_samples)}")
        print(f"  mean:     {statistics.fmean(payout_samples):.1f}")
        print(f"  stdev:    {statistics.pstdev(payout_samples):.1f}" if len(payout_samples) > 1 else "")
    else:
        print("  (no data)")

    _print_header("Vanguard frequency")
    print(f"  first-of-bounty submissions: {len(vanguard_payouts)}")
    if vanguard_payouts:
        print(f"  avg vanguard bonus awarded:  {statistics.fmean(vanguard_payouts):.1f}")

    _print_header("Per-user totals (sorted by new HP, desc)")
    print(f"  {'user':<22}{'old':>8}{'new':>8}{'Δ':>9}   rank old → rank new")
    print(f"  {'-' * 70}")
    rows = sorted(per_user_new.items(), key=lambda kv: kv[1], reverse=True)
    for uid, new_total in rows:
        old_total = user_legacy_hp.get(uid, 0)  # actual stored hps_points
        recomputed_old_total = per_user_old[uid]  # sum of stored hp_awarded
        delta = new_total - recomputed_old_total
        name = (user_names.get(uid) or f"#{uid}")[:22]
        old_rank = _get_rank_v1(recomputed_old_total)
        new_rank = get_rank_for_hp(new_total)
        arrow = "→" if old_rank != new_rank else " "
        rank_line = f"{old_rank} {arrow} {new_rank}"
        print(f"  {name:<22}{recomputed_old_total:>8}{new_total:>8}{delta:>+9}   {rank_line}")
        # Also flag drift between stored hps_points and sum-of-hp_awarded —
        # it indicates manual HP adjustments that the recompute will erase.
        if old_total != recomputed_old_total:
            print(f"  {'(stored hps_points differs:':<22} "
                  f"{old_total} vs sum of hp_awarded {recomputed_old_total})")

    _print_header("Notes")
    print("• 'old' column is SUM(hp_awarded) over the user's approved submissions,")
    print("  not the stored User.hps_points — the latter may include manual edits.")
    print("• BSK_user was rebuilt for each submission using only data from before its")
    print("  timestamp.  For chronologically-early submissions this means PP-bootstrap.")
    print("• UR=None ⇒ Ω=1.0.  Once #30 is live, all new submissions will record")
    print("  n_300/n_100/n_50 and a fresh UR, so the no-UR ratio above will decay.")
    print("• Nothing was written to the database.")


def main() -> None:
    asyncio.run(run_dryrun())


if __name__ == "__main__":
    main()
