"""Weekly bounty pool generator.

Plan: unified-giggling-tiger.

Run cadence: Monday 00:00 MSK by `tasks.bounty_weekly_generator.weekly_generator_loop`.
Also called on bot startup if the active pool is missing or expired.

Flow per invocation:
  1. Close previous active pool: set is_active=0 + expire its auto-bounties.
  2. Recompute User.weekly_tier for every registered user via get_tier_for_hp.
  3. Insert a new WeeklyBountyPool row spanning Mon..next Mon.
  4. For each tier in (C, B, A, Open):
       - Select 6 maps (SLOTS_PER_TIER) from the active map pool via
         tier_rules.pick_for_tier. 4 × 6 = 24 bounties/week total.
       - For each map: assign bounty_type+conditions, insert Bounty.
       - Mirror conditions into legacy columns so bounty_auto_checker keeps
         working without changes (min_accuracy, required_mods, max_misses).

Manual bounties (source='manual') are NEVER touched by this generator.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.bounty import Bounty
from db.models.bsk_map_pool import BskMapPool
from db.models.hps_map_pool import HpsMapPool
from db.models.user import User
from db.models.weekly_bounty_pool import WeeklyBountyPool
from services.bounty.tier_rules import (
    TIER_BSK_RANGES,
    assign_bounty_type,
    compute_bsk_map,
    pick_for_tier,
)
from utils.hp_calculator import get_tier_for_hp

logger = logging.getLogger(__name__)


# Telegram_id of the synthetic "system" creator stamped on auto bounties.
# 0 is reserved (no real Telegram account has id 0) and bounty_create.created_by
# is non-nullable.
SYSTEM_CREATED_BY = 0

TIER_ORDER = ("C", "B", "A", "Open")

# Slots per tier per week. 4 tiers × 6 = 24 bounties (was 4 × 9 = 36).
# Reduced 2026-05-29 per player feedback — smaller pool keeps each bounty
# meaningful and reduces "wiki overhead" criticism.
SLOTS_PER_TIER = 6


# ── helpers ────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _next_monday_midnight(now: datetime) -> datetime:
    """Return the next Monday 00:00 (local naive) from `now` (UTC naive)."""
    days_ahead = (7 - now.weekday()) % 7 or 7
    return (now + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _generate_auto_bounty_id(week_number: int, tier: str, slot: int) -> str:
    """Deterministic id so re-runs (e.g. tests) collide loudly rather than
    silently double-inserting."""
    today = _utcnow().strftime("%Y.%m.%d")
    return f"{today}/w{week_number:02d}-{tier}-{slot:02d}"


def _apply_conditions_to_bounty(bounty: Bounty, conditions: dict) -> None:
    """Mirror conditions dict into legacy Bounty columns where applicable.

    bounty_auto_checker._check_conditions reads from these columns, so we
    keep the legacy contract intact. Unknown keys remain only in
    bounty.conditions JSON.
    """
    if "min_accuracy" in conditions:
        bounty.min_accuracy = float(conditions["min_accuracy"])
    if "max_misses" in conditions:
        bounty.max_misses = int(conditions["max_misses"])
    if "required_mods" in conditions:
        mods = conditions["required_mods"]
        if isinstance(mods, list):
            bounty.required_mods = ",".join(str(m) for m in mods)
        else:
            bounty.required_mods = str(mods)
    # JSON blob stores the full set (including keys without legacy columns:
    # max_ur, min_combo_pct).
    bounty.conditions = json.dumps(conditions, ensure_ascii=False) if conditions else None


def _build_auto_bounty(
    *,
    bounty_id: str,
    map_row: Any,
    bounty_type: str,
    conditions: dict,
    tier: str,
    week_id: int,
    deadline: datetime,
    max_combo: int = 0,
) -> Bounty:
    """Build a Bounty row from any pool row (BskMapPool or HpsMapPool).

    Duck-typed on fields shared by both: beatmap_id, beatmapset_id, title,
    artist, version, creator, star_rating, length, ar/od/cs, bpm.  HpsMapPool
    lacks hp_drain — defaults to 0.0 when absent.
    """
    title_parts = [bounty_type, "·", tier]
    title = " ".join(title_parts) + f" — {map_row.artist} - {map_row.title}"
    if len(title) > 200:
        title = title[:197] + "..."

    bounty = Bounty(
        bounty_id=bounty_id,
        bounty_type=bounty_type,
        title=title,
        beatmap_id=map_row.beatmap_id,
        beatmapset_id=map_row.beatmapset_id,
        mapper_name=map_row.creator,
        beatmap_title=f"{map_row.artist} - {map_row.title} [{map_row.version}]",
        star_rating=float(map_row.star_rating or 0.0),
        drain_time=int(map_row.length or 0),
        cs=float(map_row.cs or 0.0),
        od=float(map_row.od or 0.0),
        ar=float(map_row.ar or 0.0),
        hp_drain=float(getattr(map_row, "hp_drain", 0.0) or 0.0),
        bpm=float(map_row.bpm or 0.0),
        max_combo=int(max_combo or getattr(map_row, "max_combo", 0) or 0),
        status="active",
        created_by=SYSTEM_CREATED_BY,
        deadline=deadline,
        source="auto",
        tier=tier,
        week_id=week_id,
    )
    _apply_conditions_to_bounty(bounty, conditions)
    return bounty


async def _fetch_beatmap_max_combo(beatmap_id: int, osu_api_client) -> int:
    """Pull max_combo via the osu! API. Returns 0 on any failure — caller
    treats 0 as "unknown" and the auto-checker's combo gate becomes neutral
    rather than always-fail. Log the miss so it's visible in operations."""
    if osu_api_client is None or not beatmap_id:
        return 0
    try:
        bm = await osu_api_client.get_beatmap(beatmap_id)
        return int((bm or {}).get("max_combo") or 0)
    except Exception as e:
        logger.warning(
            f"_fetch_beatmap_max_combo: failed for beatmap_id={beatmap_id}: {e}"
        )
        return 0


# ── public API ─────────────────────────────────────────────────────────────

async def _assign_tiers_for_all_users(session: AsyncSession) -> int:
    """Snapshot User.weekly_tier from current hps_points. Returns count."""
    users = (await session.execute(select(User))).scalars().all()
    now = _utcnow()
    for u in users:
        u.weekly_tier = get_tier_for_hp(u.hps_points or 0)
        u.weekly_tier_set_at = now
    return len(users)


async def _close_previous_pool(session: AsyncSession) -> Optional[int]:
    """Expire previous active pool + its auto-bounties. Returns old week_id."""
    old_pool = (await session.execute(
        select(WeeklyBountyPool).where(WeeklyBountyPool.is_active == 1)
    )).scalars().first()
    if old_pool is None:
        return None

    old_pool.is_active = 0
    # Expire ALL active auto-bounties, not just those tied to this pool by
    # week_id — orphaned rows (week_id=NULL or from a prior schema) must also
    # be closed so they don't accumulate in bountylist.
    await session.execute(
        update(Bounty)
        .where(Bounty.source == "auto")
        .where(Bounty.status == "active")
        .values(status="expired", closed_at=_utcnow())
    )
    return old_pool.id


async def generate_weekly_pool(
    session: AsyncSession,
    osu_api_client=None,
) -> WeeklyBountyPool:
    """Generate a new weekly pool. Caller owns the commit.

    `osu_api_client`: optional. When provided, each picked map's max_combo
    is fetched via the osu! API so the auto-checker's combo gate works for
    Marathon-style bounties. Without it, max_combo stays 0 (combo% checks
    will fall back to bounty.max_combo or skip — see auto_checker docs).
    """
    await _close_previous_pool(session)

    # Determine next week_number — monotonic across all pools, never reused.
    last_pool = (await session.execute(
        select(WeeklyBountyPool).order_by(WeeklyBountyPool.week_number.desc())
    )).scalars().first()
    next_week = (last_pool.week_number + 1) if last_pool else 1

    now = _utcnow()
    new_pool = WeeklyBountyPool(
        week_number=next_week,
        started_at=now,
        ends_at=_next_monday_midnight(now),
        is_active=1,
    )
    session.add(new_pool)
    await session.flush()  # populate new_pool.id

    # Snapshot tiers BEFORE populating bounties so the rendering layer can
    # immediately compare a user's tier against bounty.tier.
    n_users = await _assign_tiers_for_all_users(session)
    logger.info(
        f"generate_weekly_pool: snapshotted weekly_tier for {n_users} users"
    )

    # Pull all enabled maps once; tier filtering happens in pick_for_tier.
    # Plan: unified-giggling-tiger (step 8) — prefer HpsMapPool with the
    # 28-day anti-repeat cutoff so the same maps don't reappear every week.
    # Fall back to BskMapPool when HpsMapPool is empty (transitional state
    # and existing tests).
    cutoff = now - timedelta(days=28)
    maps_hps = (await session.execute(
        select(HpsMapPool).where(
            HpsMapPool.enabled == True,                       # noqa: E712
            (HpsMapPool.last_used_at == None) | (HpsMapPool.last_used_at < cutoff),  # noqa: E711
        )
    )).scalars().all()

    if maps_hps:
        maps: list[Any] = list(maps_hps)
        from_hps = True
    else:
        maps = list((await session.execute(
            select(BskMapPool).where(BskMapPool.enabled == True)  # noqa: E712
        )).scalars().all())
        from_hps = False

    deadline = new_pool.ends_at
    created_count = {tier: 0 for tier in TIER_ORDER}

    for tier in TIER_ORDER:
        picks = pick_for_tier(list(maps), tier, n=SLOTS_PER_TIER)
        if not picks:
            logger.warning(
                f"generate_weekly_pool: tier {tier!r} got 0 maps from pool "
                f"(BSK range {TIER_BSK_RANGES[tier]}). Skipping."
            )
            continue
        if len(picks) < SLOTS_PER_TIER:
            logger.warning(
                f"generate_weekly_pool: tier {tier!r} only filled "
                f"{len(picks)}/{SLOTS_PER_TIER} slots — pool needs more maps in range "
                f"{TIER_BSK_RANGES[tier]}"
            )

        for slot, map_row in enumerate(picks, start=1):
            bounty_type, conditions = assign_bounty_type(map_row, tier)
            bounty_id = _generate_auto_bounty_id(next_week, tier, slot)
            max_combo = await _fetch_beatmap_max_combo(
                map_row.beatmap_id, osu_api_client,
            )
            bounty = _build_auto_bounty(
                bounty_id=bounty_id,
                map_row=map_row,
                bounty_type=bounty_type,
                conditions=conditions,
                tier=tier,
                week_id=new_pool.id,
                deadline=deadline,
                max_combo=max_combo,
            )
            session.add(bounty)
            created_count[tier] += 1
            # Anti-repeat: only stamp HpsMapPool rows. BskMapPool has no
            # last_used_at column.
            if from_hps:
                map_row.last_used_at = now
                map_row.use_count = int(map_row.use_count or 0) + 1

    total = sum(created_count.values())
    logger.info(
        f"generate_weekly_pool: week {next_week} created {total} bounties "
        f"({created_count})"
    )
    return new_pool
