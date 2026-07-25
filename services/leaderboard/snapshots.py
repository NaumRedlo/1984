"""Capturing weekly leaderboard anchors.

`ensure_period_snapshot` is called from the background profile-updater tick
(tasks/profile_updater.py). It is a cheap no-op once the current period's rows
exist, so it can run on every tick — no separate scheduler is needed.

When a new period opens we also compute the FINAL standings of the period that
just closed and store them on the fresh rows (`prev_positions`), so the card's
▲/▼ column never has to recompute history.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from db.models.user import User
from db.models.leaderboard_snapshot import LeaderboardSnapshot
from services.leaderboard.periods import current_period_key, previous_period_key
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("services.leaderboard.snapshots")

# Metric columns mirrored from User into a snapshot row.
_ANCHOR_FIELDS = (
    "player_pp", "accuracy", "play_count", "play_time", "ranked_score", "total_hits",
)


def anchor_values(user: User) -> dict:
    """The metric values to freeze for `user` at period start."""
    return {f: getattr(user, f, None) for f in _ANCHOR_FIELDS}


async def _tenants_with_players(session) -> list[int]:
    rows = await session.execute(
        select(User.chat_id).where(User.osu_user_id.isnot(None)).distinct()
    )
    return [r[0] for r in rows.all()]


async def ensure_period_snapshot(session, *, now=None) -> int:
    """Make sure every registered player has an anchor row for the current
    period. Returns how many rows were created (0 on the common no-op path)."""
    period = current_period_key(now)
    created = 0
    for chat_id in await _tenants_with_players(session):
        created += await ensure_tenant_snapshot(session, chat_id, period=period)
    if created:
        await session.commit()
        logger.info(f"Leaderboard snapshot {period}: captured {created} rows")
    return created


async def ensure_tenant_snapshot(session, chat_id: int, *, period: str | None = None,
                                 now=None) -> int:
    """Per-tenant half of `ensure_period_snapshot` (caller commits)."""
    period = period or current_period_key(now)

    users = (await session.execute(
        select(User).where(User.chat_id == chat_id, User.osu_user_id.isnot(None))
    )).scalars().all()
    if not users:
        return 0

    have = {
        uid for (uid,) in (await session.execute(
            select(LeaderboardSnapshot.user_id).where(
                LeaderboardSnapshot.tenant_chat_id == chat_id,
                LeaderboardSnapshot.period_key == period,
            )
        )).all()
    }
    missing = [u for u in users if u.id not in have]
    if not missing:
        return 0

    # Only compute closing standings when the period actually rolls over (i.e.
    # this is the first capture for `period`); a player who registered midweek
    # just gets an anchor with no prior standing.
    prev_positions = {}
    if not have:
        prev_positions = await _closing_positions(session, chat_id, period)

    now_utc = utcnow()
    for u in missing:
        session.add(LeaderboardSnapshot(
            tenant_chat_id=chat_id, user_id=u.id, period_key=period,
            captured_at=now_utc,
            prev_positions=json.dumps(prev_positions.get(u.id)) if prev_positions.get(u.id) else None,
            **anchor_values(u),
        ))
    return len(missing)


async def _closing_positions(session, chat_id: int, period: str) -> dict[int, dict]:
    """Final standings of the period before `period`, as {user_id: {cat: pos}}.

    Computed from that period's anchors versus the values users carry right now
    — which, at rollover time, are exactly their end-of-period values.
    """
    from services.leaderboard.deltas import DELTA_CATEGORIES, compute_deltas

    prev = previous_period_key(period)
    anchors = {
        s.user_id: s for s in (await session.execute(
            select(LeaderboardSnapshot).where(
                LeaderboardSnapshot.tenant_chat_id == chat_id,
                LeaderboardSnapshot.period_key == prev,
            )
        )).scalars().all()
    }
    if not anchors:
        return {}

    users = (await session.execute(
        select(User).where(User.chat_id == chat_id, User.osu_user_id.isnot(None))
    )).scalars().all()

    positions: dict[int, dict] = {}
    for key in DELTA_CATEGORIES:
        ranked = compute_deltas(users, anchors, key)
        for pos, row in enumerate(ranked, 1):
            positions.setdefault(row["user_id"], {})[key] = pos

        # Everyone who didn't gain shares the place just past the standings.
        # Without this they'd carry no prior place at all and would come back
        # from a quiet week marked `NEW` — which reads as "first time here" for
        # someone who's been around for months. Jointly-last is honest (they
        # were outside the standings) and gives the arrow something real to
        # measure against, without inventing an order among people who all did
        # the same amount of nothing.
        outside = len(ranked) + 1
        gained = {row["user_id"] for row in ranked}
        for u in users:
            if u.id not in gained:
                positions.setdefault(u.id, {})[key] = outside
    return positions
