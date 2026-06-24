from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from sqlalchemy import select, func, or_

from db.models.best_score import UserBestScore
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from utils.timeutils import utcnow
from utils.titles import RARITY_ORDER, TITLE_REGISTRY

# Rank strings as osu! reports them. "S or better" also counts the silver/SS grades.
S_OR_BETTER = ("S", "SH", "X", "XH")
SS_RANKS = ("X", "XH")


async def _exists_best(
    session,
    user_id: int,
    *,
    min_sr: Optional[float] = None,
    max_sr: Optional[float] = None,
    ranks: Optional[Sequence[str]] = None,
    min_acc: Optional[float] = None,
    mods_all: Optional[Sequence[str]] = None,
    mods_any: Optional[Sequence[str]] = None,
) -> int:
    """Return 1 if the user has any best score matching the predicate, else 0.

    Mods are stored as a comma-joined acronym string (e.g. "HD,DT"); matching is
    a substring LIKE, which is unambiguous for osu!'s two-letter acronyms.
    """
    conds = [UserBestScore.user_id == user_id]
    if min_sr is not None:
        conds.append(UserBestScore.star_rating >= min_sr)
        conds.append(UserBestScore.star_rating.isnot(None))
    if max_sr is not None:
        conds.append(UserBestScore.star_rating <= max_sr)
        conds.append(UserBestScore.star_rating.isnot(None))
    if ranks is not None:
        conds.append(UserBestScore.rank.in_(tuple(ranks)))
    if min_acc is not None:
        conds.append(UserBestScore.accuracy >= min_acc)
    if mods_all:
        for m in mods_all:
            conds.append(UserBestScore.mods.like(f"%{m}%"))
    if mods_any:
        conds.append(or_(*[UserBestScore.mods.like(f"%{m}%") for m in mods_any]))

    stmt = select(func.count()).select_from(UserBestScore).where(*conds)
    n = (await session.execute(stmt)).scalar() or 0
    return 1 if n > 0 else 0


async def _calc_doublethink(session, user_id: int) -> int:
    """SS under EZ on a <=2* map AND a clear on a 7*+ map (two opposite skills)."""
    easy = await _exists_best(session, user_id, max_sr=2.0, ranks=SS_RANKS, mods_all=["EZ"])
    if not easy:
        return 0
    hard = await _exists_best(session, user_id, min_sr=7.0)
    return 1 if hard else 0


async def _calc_impossible(session, user_id: int) -> int:
    """Clear a map at least 2* above the user's average top-score difficulty."""
    avg = (
        await session.execute(
            select(func.avg(UserBestScore.star_rating)).where(
                UserBestScore.user_id == user_id,
                UserBestScore.star_rating.isnot(None),
            )
        )
    ).scalar()
    if avg is None:
        return 0
    return await _exists_best(session, user_id, min_sr=float(avg) + 2.0)


# title_code → callable(user, user_id, session) → int (raw value vs TitleDef.target).
# Returns either a plain int (user-stat checks) or a coroutine (DB predicates);
# refresh_user_titles awaits coroutines transparently.
_CALCULATORS = {
    # Обычный
    "registered":     lambda u, uid, s: 1 if (u.play_count or 0) > 0 else 0,
    "first_s":        lambda u, uid, s: _exists_best(s, uid, ranks=S_OR_BETTER),
    "clean_95":       lambda u, uid, s: _exists_best(s, uid, min_acc=95.0, min_sr=3.0),
    "first_4star":    lambda u, uid, s: _exists_best(s, uid, min_sr=4.0),
    "played_100":     lambda u, uid, s: u.play_count or 0,
    # Необычный
    "hd_4star":       lambda u, uid, s: _exists_best(s, uid, min_sr=4.0, mods_all=["HD"]),
    "dt_4star":       lambda u, uid, s: _exists_best(s, uid, min_sr=4.0, mods_any=["DT", "NC"]),
    "hr_45star":      lambda u, uid, s: _exists_best(s, uid, min_sr=4.5, mods_all=["HR"]),
    "acc_99":         lambda u, uid, s: _exists_best(s, uid, min_acc=99.0, min_sr=4.0),
    # Редкий
    "ss_4star":       lambda u, uid, s: _exists_best(s, uid, min_sr=4.0, ranks=SS_RANKS),
    "hdhr_5star":     lambda u, uid, s: _exists_best(s, uid, min_sr=5.0, mods_all=["HD", "HR"]),
    "acc_995":        lambda u, uid, s: _exists_best(s, uid, min_acc=99.5, min_sr=6.0),
    # Эпический
    "fl_6star":       lambda u, uid, s: _exists_best(s, uid, min_sr=6.0, mods_all=["FL"]),
    "ss_6star":       lambda u, uid, s: _exists_best(s, uid, min_sr=6.0, ranks=SS_RANKS),
    "hddt_65star":    lambda u, uid, s: _exists_best(s, uid, min_sr=6.5, mods_all=["HD"], mods_any=["DT", "NC"]),
    "ss_hd_55star":   lambda u, uid, s: _exists_best(s, uid, min_sr=5.5, ranks=SS_RANKS, mods_all=["HD"]),
    # Легендарный
    "ss_7star":       lambda u, uid, s: _exists_best(s, uid, min_sr=7.0, ranks=SS_RANKS),
    "ss_fl_55star":   lambda u, uid, s: _exists_best(s, uid, min_sr=5.5, ranks=SS_RANKS, mods_all=["FL"]),
    "ss_hdhr_6star":  lambda u, uid, s: _exists_best(s, uid, min_sr=6.0, ranks=SS_RANKS, mods_all=["HD", "HR"]),
    # Мифический
    "ss_8star":       lambda u, uid, s: _exists_best(s, uid, min_sr=8.0, ranks=SS_RANKS),
    "ss_hddt_75star": lambda u, uid, s: _exists_best(s, uid, min_sr=7.5, ranks=SS_RANKS, mods_all=["HD"], mods_any=["DT", "NC"]),
    "ss_fl_7star":    lambda u, uid, s: _exists_best(s, uid, min_sr=7.0, ranks=SS_RANKS, mods_all=["FL"]),
    # Секретный
    "doublethink":        lambda u, uid, s: _calc_doublethink(s, uid),
    "impossible_number":  lambda u, uid, s: _calc_impossible(s, uid),
}


async def refresh_user_titles(user: User, session) -> List[Dict]:
    """Recalculate all title progress for user. Returns list of progress dicts
    in registry (rarity-ascending) order. Caller must commit.
    """
    stmt = select(UserTitleProgress).where(UserTitleProgress.user_id == user.id)
    result = await session.execute(stmt)
    existing = {p.title_code: p for p in result.scalars().all()}

    progress_list = []

    for code, title_def in TITLE_REGISTRY.items():
        calc = _CALCULATORS.get(code)
        if not calc:
            continue

        raw = calc(user, user.id, session)
        current = await raw if hasattr(raw, "__await__") else raw

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

        if current >= title_def.target and not prog.unlocked:
            prog.unlocked = True
            prog.unlocked_at = utcnow()

        if title_def.target > 0:
            pct = min(current / title_def.target * 100, 100.0)
        else:
            pct = 100.0 if current >= 1 else 0.0

        progress_list.append({
            "code": code,
            "name": title_def.name,
            "description": title_def.description,
            "flavor": title_def.flavor,
            "target": title_def.target,
            "current": current,
            "progress_pct": pct,
            "unlocked": prog.unlocked,
            "unlocked_at": prog.unlocked_at,
            "color": title_def.color,
            "rarity": title_def.rarity,
            "rarity_label": title_def.rarity_label,
            "secret": title_def.secret,
            "is_active": user.active_title_code == code,
        })

    return progress_list


def build_titles_summary(progress_list: List[Dict]) -> Dict:
    """Aggregate a refresh_user_titles() result into dashboard summary fields:
    overall counts, per-rarity counts, hardest-tier / latest / next-up titles.
    """
    total = len(progress_list)
    unlocked_items = [p for p in progress_list if p["unlocked"]]
    unlocked = len(unlocked_items)

    by_rarity = {r: {"unlocked": 0, "total": 0} for r in RARITY_ORDER}
    for p in progress_list:
        bucket = by_rarity[p["rarity"]]
        bucket["total"] += 1
        if p["unlocked"]:
            bucket["unlocked"] += 1

    rarest = max(
        unlocked_items,
        key=lambda p: RARITY_ORDER.index(p["rarity"]),
        default=None,
    )
    latest = max(
        (p for p in unlocked_items if p["unlocked_at"]),
        key=lambda p: p["unlocked_at"],
        default=None,
    )
    next_up = max(
        (p for p in progress_list if not p["unlocked"]),
        key=lambda p: (p["progress_pct"], -RARITY_ORDER.index(p["rarity"])),
        default=None,
    )

    return {
        "total": total,
        "unlocked": unlocked,
        "overall_pct": round(unlocked / total * 100, 1) if total else 0.0,
        "by_rarity": by_rarity,
        "rarest": rarest,
        "latest": latest,
        "next_up": next_up,
    }


async def calc_title_rarity(title_code: str, session) -> float:
    """Return % of registered users who have unlocked this title."""
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
            UserTitleProgress.unlocked == True,  # noqa: E712
        )
    )
    unlocked = (await session.execute(unlocked_stmt)).scalar() or 0
    return round(unlocked / total * 100, 1)
