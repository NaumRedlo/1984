from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import select, func, and_

from config.settings import CONTRIBUTOR_IDS
from db.models.best_score import UserBestScore
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from utils.titles import TITLE_REGISTRY

S_RANKS = ("S", "SH", "X", "XH")


async def _calc_star_hunter(user_id: int, session) -> int:
    """Count best scores on 5*+ maps with rank S or higher."""
    stmt = (
        select(func.count())
        .select_from(UserBestScore)
        .where(
            UserBestScore.user_id == user_id,
            UserBestScore.star_rating >= 5.0,
            UserBestScore.star_rating.isnot(None),
            UserBestScore.rank.in_(S_RANKS),
        )
    )
    result = await session.execute(stmt)
    return result.scalar() or 0


def _calc_score_lord(user: User) -> int:
    """Return user's total_score (raw value, compared against target)."""
    return user.total_score or 0


def _calc_contributor(user: User) -> int:
    """1 if user is in CONTRIBUTOR_IDS, else 0."""
    return 1 if user.telegram_id in CONTRIBUTOR_IDS else 0


# Maps title_code → calculator callable
_CALCULATORS = {
    "star_hunter": lambda user, uid, s: _calc_star_hunter(uid, s),
    "score_lord": lambda user, uid, s: _calc_score_lord(user),
    "contributor": lambda user, uid, s: _calc_contributor(user),
}


async def refresh_user_titles(user: User, session) -> List[Dict]:
    """Recalculate all title progress for user. Returns list of progress dicts.

    Each dict: {code, name, description, target, current, progress_pct, unlocked, color, is_active}
    Caller must commit.
    """
    # Load existing progress rows
    stmt = select(UserTitleProgress).where(UserTitleProgress.user_id == user.id)
    result = await session.execute(stmt)
    existing = {p.title_code: p for p in result.scalars().all()}

    progress_list = []

    for code, title_def in TITLE_REGISTRY.items():
        calc = _CALCULATORS.get(code)
        if not calc:
            continue

        # Calculate current value
        raw = calc(user, user.id, session)
        # Await if coroutine
        if hasattr(raw, "__await__"):
            current = await raw
        else:
            current = raw

        # Upsert progress row
        prog = existing.get(code)
        if not prog:
            prog = UserTitleProgress(
                user_id=user.id,
                title_code=code,
                current_value=current,
                unlocked=False,
            )
            session.add(prog)
            existing[code] = prog

        prog.current_value = current

        just_unlocked = False
        if current >= title_def.target and not prog.unlocked:
            prog.unlocked = True
            prog.unlocked_at = datetime.now(timezone.utc)
            just_unlocked = True

        # Progress percentage (capped at 100)
        if title_def.target > 0:
            pct = min(current / title_def.target * 100, 100.0)
        else:
            pct = 100.0 if current >= 1 else 0.0

        progress_list.append({
            "code": code,
            "name": title_def.name,
            "description": title_def.description,
            "target": title_def.target,
            "current": current,
            "progress_pct": pct,
            "unlocked": prog.unlocked,
            "color": title_def.color,
            "is_active": user.active_title_code == code,
        })

    return progress_list


async def calc_title_rarity(title_code: str, session) -> float:
    """Return % of registered users who unlocked this title."""
    total_stmt = (
        select(func.count())
        .select_from(User)
        .where(User.osu_user_id.isnot(None))
    )
    total = (await session.execute(total_stmt)).scalar() or 0
    if total == 0:
        return 0.0

    unlocked_stmt = (
        select(func.count())
        .select_from(UserTitleProgress)
        .where(
            UserTitleProgress.title_code == title_code,
            UserTitleProgress.unlocked == True,
        )
    )
    unlocked = (await session.execute(unlocked_stmt)).scalar() or 0
    return round(unlocked / total * 100, 1)
