from __future__ import annotations

from datetime import timedelta
from typing import Dict, List

from sqlalchemy import select, func, or_, and_

from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from utils.timeutils import utcnow
from utils.titles import RARITY_ORDER, TITLE_REGISTRY, TitleDef

# Rank strings as osu! reports them. "S or better" also counts the silver/SS grades.
S_OR_BETTER = ("S", "SH", "X", "XH")
SS_RANKS = ("X", "XH")


def _model_conds(M, user_id, crit, *, require_passed):
    """SQL conditions on one score-like table for a criteria dict.

    Mods are a comma-joined acronym string ("HD,DT") matched by substring LIKE —
    unambiguous for osu!'s two-letter acronyms. NULL per-play fields fail the
    comparisons and are excluded. `require_passed` constrains map_attempts (which
    may hold logged fails) to clears; all current titles are clears.
    """
    conds = [M.user_id == user_id]
    if require_passed:
        conds.append(M.passed.is_(True))
    if crit.get("min_sr") is not None:
        conds.append(M.star_rating >= crit["min_sr"])
        conds.append(M.star_rating.isnot(None))
    if crit.get("max_sr") is not None:
        conds.append(M.star_rating <= crit["max_sr"])
        conds.append(M.star_rating.isnot(None))
    if crit.get("ranks") is not None:
        conds.append(M.rank.in_(tuple(crit["ranks"])))
    if crit.get("min_acc") is not None:
        conds.append(M.accuracy >= crit["min_acc"])
    for m in (crit.get("mods_all") or []):
        conds.append(M.mods.like(f"%{m}%"))
    if crit.get("mods_any"):
        conds.append(or_(*[M.mods.like(f"%{m}%") for m in crit["mods_any"]]))
    if crit.get("min_bpm") is not None:
        conds.append(M.bpm >= crit["min_bpm"])
    if crit.get("min_length") is not None:
        conds.append(M.length >= crit["min_length"])
    if crit.get("max_length") is not None:
        conds.append(M.length > 0)
        conds.append(M.length <= crit["max_length"])
    if crit.get("fc"):
        # Primary signal: the API's perfect-combo flag. The combo comparison is
        # only a fallback for rows where the flag is unknown (NULL) — never when
        # the flag explicitly says it was NOT a full combo.
        conds.append(or_(
            M.is_fc.is_(True),
            and_(M.is_fc.is_(None), M.count_miss == 0,
                 M.map_max_combo.isnot(None), M.max_combo >= M.map_max_combo),
        ))
    if crit.get("max_miss") is not None:
        conds.append(M.count_miss <= crit["max_miss"])
    if crit.get("max_100") is not None:
        conds.append(M.count_100 <= crit["max_100"])
    return conds


async def _exists_best(session, user_id, **crit) -> int:
    """1 if any score in best_scores ∪ observed map_attempts matches, else 0."""
    for M, require_passed in ((UserBestScore, False), (UserMapAttempt, True)):
        conds = _model_conds(M, user_id, crit, require_passed=require_passed)
        n = (await session.execute(select(func.count()).select_from(M).where(*conds))).scalar() or 0
        if n > 0:
            return 1
    return 0


def _play_matches(play: Dict, **crit) -> bool:
    """Evaluate the same criteria against a single observed play (in memory).
    A non-passed play matches nothing — all current titles are clears."""
    if not play.get("passed"):
        return False
    sr = play.get("star_rating")
    if crit.get("min_sr") is not None and (sr is None or sr < crit["min_sr"]):
        return False
    if crit.get("max_sr") is not None and (sr is None or sr > crit["max_sr"]):
        return False
    if crit.get("ranks") is not None and (play.get("rank") or "") not in crit["ranks"]:
        return False
    if crit.get("min_acc") is not None and (play.get("accuracy") or 0) < crit["min_acc"]:
        return False
    mods = play.get("mods") or ""
    if any(m not in mods for m in (crit.get("mods_all") or [])):
        return False
    if crit.get("mods_any") and not any(m in mods for m in crit["mods_any"]):
        return False
    if crit.get("min_bpm") is not None and (play.get("bpm") or 0) < crit["min_bpm"]:
        return False
    if crit.get("min_length") is not None and (play.get("length") or 0) < crit["min_length"]:
        return False
    if crit.get("max_length") is not None:
        ln = play.get("length")
        if ln is None or ln <= 0 or ln > crit["max_length"]:
            return False
    if crit.get("fc"):
        fc = play.get("is_fc")
        if fc is False:
            return False
        if fc is not True:  # unknown → fall back to the combo comparison
            mmc = play.get("map_max_combo")
            if (play.get("count_miss") or 0) != 0 or not mmc or (play.get("max_combo") or 0) < mmc:
                return False
    miss = play.get("count_miss")
    if crit.get("max_miss") is not None and (miss is None or miss > crit["max_miss"]):
        return False
    n100 = play.get("count_100")
    if crit.get("max_100") is not None and (n100 is None or n100 > crit["max_100"]):
        return False
    return True


# Simple titles whose condition is a single criteria dict — shared by the bulk
# aggregate (_exists_best over the corpus) and the per-play matcher. Stat-based
# (registered/played_100) and compound/secret titles are handled bespoke below.
TITLE_CRITERIA: Dict[str, dict] = {
    # Wave 1 — computable now (stored best/attempt fields + user stats).
    "rank_d":         dict(ranks=("D",)),
    "short_30":       dict(max_length=30),
    "td_4star":       dict(min_sr=4.0, mods_all=["TD"]),
    "fl_6star":       dict(min_sr=6.0, mods_all=["FL"]),
    "fc_len_5m":      dict(fc=True, min_length=300),
    "fc_bpm_210":     dict(fc=True, min_bpm=210.0),
    "ss_7star":       dict(min_sr=7.0, ranks=SS_RANKS),
    "ss_fl_55star":   dict(min_sr=6.0, ranks=SS_RANKS, mods_all=["FL"]),
    "ss_8star":       dict(min_sr=8.5, ranks=SS_RANKS),
    "ss_hddt_75star": dict(min_sr=8.0, ranks=SS_RANKS, mods_all=["HD"], mods_any=["DT", "NC"]),
    "fc_bpm_250":     dict(fc=True, min_bpm=250.0, min_sr=7.0),
    "fc_marathon_30m": dict(fc=True, min_length=1800, min_sr=5.5),
}


async def _calc_doublethink(session, user_id: int) -> int:
    """SS under EZ on a <=2* map AND a clear on a 7*+ map (two opposite skills)."""
    easy = await _exists_best(session, user_id, max_sr=2.0, ranks=SS_RANKS, mods_all=["EZ"])
    if not easy:
        return 0
    hard = await _exists_best(session, user_id, min_sr=7.0)
    return 1 if hard else 0


# ── Wave 2: index aggregates (streaks / per-map counts / history) ──────────
# These read the growing recent-play index (UserMapAttempt), optionally unioned
# with best_scores. Streaks need play order, so they use played_at and therefore
# only the attempts table (best_scores carry no timestamp). All non-secret, so
# they unlock in the bulk refresh; live evaluation is not wired for them.

async def _corpus_rank_map(session, uid):
    """beatmap_id → set of ranks seen for the user across best ∪ attempts."""
    by_map: Dict[int, set] = {}
    for M in (UserBestScore, UserMapAttempt):
        rows = (await session.execute(
            select(M.beatmap_id, M.rank).where(M.user_id == uid, M.rank.isnot(None))
        )).all()
        for bid, rank in rows:
            by_map.setdefault(bid, set()).add(rank)
    return by_map


async def _calc_broken_record(session, uid) -> int:
    """Most times the user has played any single map (all attempts count)."""
    counts = (await session.execute(
        select(func.count()).select_from(UserMapAttempt)
        .where(UserMapAttempt.user_id == uid)
        .group_by(UserMapAttempt.beatmap_id)
    )).scalars().all()
    return max(counts) if counts else 0


async def _calc_off_day(session, uid) -> int:
    """Most fails the user has logged on a single map."""
    counts = (await session.execute(
        select(func.count()).select_from(UserMapAttempt)
        .where(UserMapAttempt.user_id == uid, UserMapAttempt.passed.is_(False))
        .group_by(UserMapAttempt.beatmap_id)
    )).scalars().all()
    return max(counts) if counts else 0


async def _calc_perfectionist(session, uid) -> int:
    """A map carrying both a plain S/SH and an SS (X/XH) — improved on replay."""
    for ranks in (await _corpus_rank_map(session, uid)).values():
        if ranks & {"S", "SH"} and ranks & set(SS_RANKS):
            return 1
    return 0


async def _calc_reeducated(session, uid) -> int:
    """A map carrying both a D and an A-or-better — fall then correction."""
    a_or_better = {"A"} | set(S_OR_BETTER)
    for ranks in (await _corpus_rank_map(session, uid)).values():
        if "D" in ranks and ranks & a_or_better:
            return 1
    return 0


async def _calc_dejavu(session, uid) -> int:
    """Same exact score value on two distinct maps."""
    by_score: Dict[int, set] = {}
    for M in (UserBestScore, UserMapAttempt):
        rows = (await session.execute(
            select(M.score, M.beatmap_id).where(
                M.user_id == uid, M.score.isnot(None), M.score > 0)
        )).all()
        for sc, bid in rows:
            by_score.setdefault(sc, set()).add(bid)
    return 1 if any(len(bids) >= 2 for bids in by_score.values()) else 0


async def _calc_wysi(session, uid) -> int:
    """A combo containing 727 (the iconic WYSI)."""
    for M in (UserBestScore, UserMapAttempt):
        combos = (await session.execute(
            select(M.max_combo).where(M.user_id == uid, M.max_combo.isnot(None))
        )).scalars().all()
        if any("727" in str(c) for c in combos):
            return 1
    return 0


async def _longest_run(session, uid, predicate, *, need_rank=False, need_acc=False):
    """Longest run of consecutive attempts (played_at order) satisfying predicate.
    The index has gaps, so this approximates a true play streak."""
    cols = [UserMapAttempt.played_at]
    cols.append(UserMapAttempt.rank if need_rank else UserMapAttempt.accuracy)
    conds = [UserMapAttempt.user_id == uid, UserMapAttempt.played_at.isnot(None)]
    if need_acc:
        conds.append(UserMapAttempt.accuracy.isnot(None))
    rows = (await session.execute(
        select(*cols).where(*conds).order_by(UserMapAttempt.played_at)
    )).all()
    best = run = 0
    for _, val in rows:
        if predicate(val):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


async def _calc_ss_streak(session, uid) -> int:
    return await _longest_run(session, uid, lambda r: r in SS_RANKS, need_rank=True)


async def _calc_lowacc_streak(session, uid) -> int:
    return await _longest_run(session, uid, lambda a: a < 90.0, need_acc=True)


# ── Wave 3: stored metadata (status / ranked_date / supporter / ranked_score) ──
_ARCHAEOLOGY_AGE = timedelta(days=365 * 12 + 3)   # ~12 years
_ARCHIVIST_RANKED_SCORE = 500_000_000_000          # "colossal" — tunable knob


async def _calc_graveyard(session, uid) -> int:
    """Played any map with Graveyard status (a pass is not required — 'play')."""
    for M in (UserBestScore, UserMapAttempt):
        n = (await session.execute(
            select(func.count()).select_from(M)
            .where(M.user_id == uid, M.status == "graveyard")
        )).scalar() or 0
        if n:
            return 1
    return 0


async def _calc_archaeologist(session, uid) -> int:
    """Passed a map ranked at least ~12 years ago."""
    cutoff = utcnow() - _ARCHAEOLOGY_AGE
    checks = (
        (UserBestScore, [UserBestScore.user_id == uid]),                      # best = passes
        (UserMapAttempt, [UserMapAttempt.user_id == uid, UserMapAttempt.passed.is_(True)]),
    )
    for M, conds in checks:
        n = (await session.execute(
            select(func.count()).select_from(M).where(
                *conds, M.ranked_date.isnot(None), M.ranked_date <= cutoff)
        )).scalar() or 0
        if n:
            return 1
    return 0


def _crit_calc(crit):
    async def _c(u, uid, s):
        return await _exists_best(s, uid, **crit)
    return _c


# title_code → callable(user, user_id, session) → int. Returns a plain int
# (user-stat checks) or a coroutine (DB predicates); refresh_user_titles awaits.
_CALCULATORS = {code: _crit_calc(crit) for code, crit in TITLE_CRITERIA.items()}
_CALCULATORS.update({
    "registered":   lambda u, uid, s: 1 if (u.play_count or 0) > 0 else 0,
    "played_100k":  lambda u, uid, s: u.play_count or 0,
    "doublethink":  lambda u, uid, s: _calc_doublethink(s, uid),
    # Wave 2 — index aggregates.
    "broken_record": lambda u, uid, s: _calc_broken_record(s, uid),
    "off_day":       lambda u, uid, s: _calc_off_day(s, uid),
    "perfectionist": lambda u, uid, s: _calc_perfectionist(s, uid),
    "reeducated":    lambda u, uid, s: _calc_reeducated(s, uid),
    "dejavu":        lambda u, uid, s: _calc_dejavu(s, uid),
    "wysi":          lambda u, uid, s: _calc_wysi(s, uid),
    "ss_streak_10":  lambda u, uid, s: _calc_ss_streak(s, uid),
    "lowacc_streak_10": lambda u, uid, s: _calc_lowacc_streak(s, uid),
    # Wave 3 — stored metadata.
    "volunteer":     lambda u, uid, s: 1 if getattr(u, "is_supporter", False) else 0,
    "archivist":     lambda u, uid, s: 1 if (u.ranked_score or 0) >= _ARCHIVIST_RANKED_SCORE else 0,
    "graveyard":     lambda u, uid, s: _calc_graveyard(s, uid),
    "archaeologist": lambda u, uid, s: _calc_archaeologist(s, uid),
})


async def _play_unlocks(code: str, play: Dict, user: User, session) -> bool:
    """Does this single observed play unlock `code` for the user? Secrets require
    the play itself to be a qualifying play (history fills only the other half)."""
    crit = TITLE_CRITERIA.get(code)
    if crit is not None:
        return _play_matches(play, **crit)
    if code == "doublethink":
        if not play.get("passed"):
            return False
        if _play_matches(play, max_sr=2.0, ranks=SS_RANKS, mods_all=["EZ"]):
            return bool(await _exists_best(session, user.id, min_sr=7.0))
        if _play_matches(play, min_sr=7.0):
            return bool(await _exists_best(session, user.id, max_sr=2.0, ranks=SS_RANKS, mods_all=["EZ"]))
        return False
    return False  # registered / played_100k aren't per-play unlocks


async def evaluate_recent_plays(user: User, plays: List[Dict], session) -> List[TitleDef]:
    """Unlock any titles satisfied by ANY of these observed plays; return them.

    This is the ONLY path that unlocks secret titles, and only when an observed
    play itself qualifies — so a secret can't be shaken out of old history.
    Loads progress once. Caller must commit.
    """
    if not plays:
        return []
    rows = {
        p.title_code: p
        for p in (
            await session.execute(
                select(UserTitleProgress).where(UserTitleProgress.user_id == user.id)
            )
        ).scalars().all()
    }
    newly: List[TitleDef] = []
    for code, td in TITLE_REGISTRY.items():
        prog = rows.get(code)
        if prog and prog.unlocked:
            continue
        unlocked_now = False
        for play in plays:
            if await _play_unlocks(code, play, user, session):
                unlocked_now = True
                break
        if not unlocked_now:
            continue
        if not prog:
            prog = UserTitleProgress(user_id=user.id, title_code=code,
                                     current_value=td.target, unlocked=False)
            session.add(prog)
            rows[code] = prog
        prog.current_value = max(prog.current_value or 0, td.target)
        prog.unlocked = True
        prog.unlocked_at = utcnow()
        newly.append(td)
    return newly


async def evaluate_recent_play(user: User, play: Dict, session) -> List[TitleDef]:
    """Single-play convenience wrapper around evaluate_recent_plays."""
    return await evaluate_recent_plays(user, [play], session)


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

        # Secret titles never auto-unlock from the bulk corpus scan — they're
        # earned live via evaluate_recent_play (the player must actually do it).
        if current >= title_def.target and not prog.unlocked and not title_def.secret:
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
