from __future__ import annotations

from datetime import timedelta
from typing import Dict, List

from sqlalchemy import select, func, or_, and_

from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from utils.timeutils import utcnow
from utils.title_history import UNRANKED_MODS, forget_history, load_history, mod_set
from utils.title_rules import RULES, fits
from utils.titles import RARITY_ORDER, TITLE_REGISTRY, TitleDef

S_OR_BETTER = ("S", "SH", "X", "XH")
SS_RANKS = ("X", "XH")

def _model_conds(M, player_id, crit, *, require_passed):
    conds = [M.player_id == player_id]
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
        conds.append(or_(
            M.is_fc.is_(True),
            and_(M.is_fc.is_(None), M.count_miss == 0,
                 M.map_max_combo.isnot(None), M.max_combo >= M.map_max_combo),
        ))
    if crit.get("max_miss") is not None:
        conds.append(M.count_miss <= crit["max_miss"])
    if crit.get("max_100") is not None:
        conds.append(M.count_100 <= crit["max_100"])
    conds.append(or_(M.mods.is_(None), and_(*[~M.mods.like(f"%{m}%") for m in sorted(UNRANKED_MODS)])))
    return conds

async def _exists_best(session, player_id, **crit) -> int:
    for M, require_passed in ((UserBestScore, False), (UserMapAttempt, True)):
        conds = _model_conds(M, player_id, crit, require_passed=require_passed)
        n = (await session.execute(select(func.count()).select_from(M).where(*conds))).scalar() or 0
        if n > 0:
            return 1
    return 0

_mod_set = mod_set

def _play_matches(play: Dict, **crit) -> bool:
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
    mods = _mod_set(play.get("mods"))
    if mods & UNRANKED_MODS:
        return False
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
        if fc is not True:
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

TITLE_CRITERIA: Dict[str, dict] = {
    "rank_d":         dict(ranks=("D",)),
    "short_30":       dict(max_length=30),
    "td_4star":       dict(min_sr=4.0, mods_all=["TD"]),
    "fl_6star":       dict(min_sr=6.0, mods_all=["FL"]),
    "fc_len_5m":      dict(fc=True, min_length=480, min_sr=5.0),
    "ss_7star":       dict(min_sr=7.0, ranks=SS_RANKS),
    "ss_fl_55star":   dict(min_sr=6.0, ranks=SS_RANKS, mods_all=["FL"]),
    "ss_8star":       dict(min_sr=8.5, ranks=SS_RANKS),
    "ss_hddt_75star": dict(min_sr=8.0, ranks=SS_RANKS, mods_all=["HD"], mods_any=["DT", "NC"]),
    "fc_marathon_30m": dict(fc=True, min_length=1800, min_sr=5.5),
    "ss_hdfl_5":      dict(min_sr=5.0, ranks=SS_RANKS, mods_all=["HD", "FL"]),
    "ez_pass_7":      dict(min_sr=7.0, mods_all=["EZ"]),
}

async def _calc_doublethink(session, player_id: int) -> int:
    easy = await _exists_best(session, player_id, max_sr=2.0, ranks=SS_RANKS, mods_all=["EZ"])
    if not easy:
        return 0
    hard = await _exists_best(session, player_id, min_sr=7.0)
    return 1 if hard else 0

async def _calc_broken_record(session, uid) -> int:
    counts = (await session.execute(
        select(func.count()).select_from(UserMapAttempt)
        .where(UserMapAttempt.player_id == uid)
        .group_by(UserMapAttempt.beatmap_id)
    )).scalars().all()
    return max(counts) if counts else 0

async def _calc_wysi(session, uid) -> int:
    for M in (UserBestScore, UserMapAttempt):
        combos = (await session.execute(
            select(M.max_combo).where(M.player_id == uid, M.max_combo.isnot(None))
        )).scalars().all()
        if any("727" in str(c) for c in combos):
            return 1
    return 0

_ARCHAEOLOGY_AGE = timedelta(days=365 * 12 + 3)
_ARCHIVIST_MIN_PEERS = 3

async def _calc_graveyard(session, uid) -> int:
    for M in (UserBestScore, UserMapAttempt):
        n = (await session.execute(
            select(func.count()).select_from(M)
            .where(M.player_id == uid, M.status == "graveyard")
        )).scalar() or 0
        if n:
            return 1
    return 0

async def _calc_archaeologist(session, uid) -> int:
    cutoff = utcnow() - _ARCHAEOLOGY_AGE
    checks = (
        (UserBestScore, [UserBestScore.player_id == uid]),
        (UserMapAttempt, [UserMapAttempt.player_id == uid, UserMapAttempt.passed.is_(True)]),
    )
    for M, conds in checks:
        n = (await session.execute(
            select(func.count()).select_from(M).where(
                *conds, M.ranked_date.isnot(None), M.ranked_date <= cutoff)
        )).scalar() or 0
        if n:
            return 1
    return 0

_BOARD_METRICS = (
    lambda u: u.player_pp or 0,
    lambda u: u.accuracy or 0.0,
    lambda u: u.play_count or 0,
    lambda u: (u.play_time or 0) // 3600,
    lambda u: u.ranked_score or 0,
    lambda u: (u.total_hits or 0) / u.play_count if u.play_count else 0.0,
)

async def _calc_big_brother(session, u) -> int:
    if getattr(u, "chat_id", None) is None:
        return 0
    peers = (await session.execute(
        select(User).where(User.chat_id == u.chat_id, User.osu_user_id.isnot(None))
    )).scalars().all()
    if len(peers) < _ARCHIVIST_MIN_PEERS or not any(peer.id == u.id for peer in peers):
        return 0
    led = 0
    for metric in _BOARD_METRICS:
        mine = metric(u)
        if mine > 0 and all(mine > metric(peer) for peer in peers if peer.id != u.id):
            led += 1
    return led

async def _calc_archivist(session, u) -> int:
    mine = u.ranked_score or 0
    if mine <= 0 or getattr(u, "chat_id", None) is None:
        return 0
    linked = (User.chat_id == u.chat_id, User.osu_user_id.isnot(None))
    peers = (await session.execute(
        select(func.count()).select_from(User).where(*linked)
    )).scalar() or 0
    if peers < _ARCHIVIST_MIN_PEERS:
        return 0
    top = (await session.execute(
        select(func.max(User.ranked_score)).where(*linked)
    )).scalar() or 0
    return 1 if mine >= top else 0

_SESSION_GAP = timedelta(minutes=30)

async def _sessions(session, uid):
    rows = (await session.execute(
        select(UserMapAttempt.played_at, UserMapAttempt.beatmap_id)
        .where(UserMapAttempt.player_id == uid, UserMapAttempt.played_at.isnot(None))
        .order_by(UserMapAttempt.played_at)
    )).all()
    out, cur, prev = [], [], None
    for played_at, bid in rows:
        if prev is not None and (played_at - prev) > _SESSION_GAP:
            out.append(cur)
            cur = []
        cur.append((played_at, bid))
        prev = played_at
    if cur:
        out.append(cur)
    return out

async def _calc_stuck_loop(session, uid) -> int:
    best = 0
    for s in await _sessions(session, uid):
        run, prev_bid = 0, None
        for _, bid in s:
            run = run + 1 if bid == prev_bid else 1
            prev_bid = bid
            best = max(best, run)
    return best

async def _stuck_loop_tail(session, uid):
    rows = (await session.execute(
        select(UserMapAttempt.played_at, UserMapAttempt.beatmap_id)
        .where(UserMapAttempt.player_id == uid, UserMapAttempt.played_at.isnot(None))
        .order_by(UserMapAttempt.played_at.desc())
    )).all()
    run, prev, target_bid = 0, None, None
    for played_at, bid in rows:
        if target_bid is None:
            target_bid, run, prev = bid, 1, played_at
            continue
        if bid == target_bid and prev is not None and (prev - played_at) <= _SESSION_GAP:
            run += 1
            prev = played_at
        else:
            break
    return run, target_bid

_COMEBACK_GAP = timedelta(days=180)
_WEEK = timedelta(days=7)

def bump_profile_opens(user) -> None:
    today = utcnow().date()
    if user.profile_opens_date != today:
        user.profile_opens_date = today
        user.profile_opens_count = 0
    user.profile_opens_count = (user.profile_opens_count or 0) + 1
    user.profile_opens_best = max(user.profile_opens_best or 0, user.profile_opens_count)

def touch_activity_day(user, today=None) -> None:
    today = today or utcnow().date()
    last = user.active_day
    if last is not None and last >= today:
        return
    if last is not None and (today - last).days == 1:
        user.active_streak = (user.active_streak or 0) + 1
    else:
        user.active_streak = 1
    user.active_day = today
    user.active_streak_best = max(user.active_streak_best or 0, user.active_streak)

def detect_comeback(user) -> bool:
    last = user.last_seen_at
    if last is not None and not user.comeback_done and (utcnow() - last) >= _COMEBACK_GAP:
        user.comeback_done = True
        return True
    return False

def update_weekly_plays(user) -> None:
    now = utcnow()
    pc = user.play_count or 0
    anchor_at = user.playcount_week_anchor_at
    if anchor_at is None or (now - anchor_at) >= _WEEK:
        if anchor_at is not None and user.playcount_week_anchor is not None:
            user.week_plays_best = max(user.week_plays_best or 0, max(0, pc - user.playcount_week_anchor))
        user.playcount_week_anchor = pc
        user.playcount_week_anchor_at = now
        return
    delta = max(0, pc - (user.playcount_week_anchor or pc))
    user.week_plays_best = max(user.week_plays_best or 0, delta)

async def unlock_title(user, code: str, session, *, value=None) -> bool:
    td = TITLE_REGISTRY.get(code)
    if td is None or user.player_id is None:
        return False
    prog = (await _ensure_progress_rows(session, user.player_id))[code]
    if prog.unlocked:
        return False
    prog.current_value = value if value is not None else (td.target or 1)
    prog.unlocked = True
    prog.unlocked_at = utcnow()
    return True

_LAST_NOTE_PCT = 95.0
_CHOKE_COMBO_RATIO = 0.95
_CHOKE_MIN_ACC = 99.0

def _has_jackpot(score) -> bool:
    try:
        return "777,777" in f"{int(score):,}"
    except (TypeError, ValueError):
        return False

async def _calc_magic7(session, uid) -> int:
    for M in (UserBestScore, UserMapAttempt):
        scores = (await session.execute(
            select(M.score).where(M.player_id == uid, M.score.isnot(None))
        )).scalars().all()
        if any(_has_jackpot(sc) for sc in scores):
            return 1
    return 0

async def _calc_choke(session, uid) -> int:
    for M in (UserBestScore, UserMapAttempt):
        rows = (await session.execute(
            select(M.max_combo, M.map_max_combo, M.accuracy).where(
                M.player_id == uid, M.max_combo.isnot(None),
                M.map_max_combo.isnot(None), M.map_max_combo > 0,
                M.accuracy >= _CHOKE_MIN_ACC)
        )).all()
        if any(_CHOKE_COMBO_RATIO * mmc <= mc < mmc for mc, mmc, _ in rows):
            return 1
    return 0

async def _calc_last_note(session, uid) -> int:
    rows = (await session.execute(
        select(UserMapAttempt.count_300, UserMapAttempt.count_100,
               UserMapAttempt.count_50, UserMapAttempt.count_miss,
               UserMapAttempt.total_objects)
        .where(UserMapAttempt.player_id == uid,
               UserMapAttempt.passed.is_(False),
               UserMapAttempt.total_objects.isnot(None),
               UserMapAttempt.total_objects > 0)
    )).all()
    for c3, c1, c5, cm, total in rows:
        hit = (c3 or 0) + (c1 or 0) + (c5 or 0) + (cm or 0)
        if hit / total * 100.0 >= _LAST_NOTE_PCT:
            return 1
    return 0

_NOT_MASKS = {"CL", "NM"}

async def _calc_masks(session, uid) -> int:
    seen: set[str] = set()
    for M in (UserBestScore, UserMapAttempt):
        for mstr in (await session.execute(
            select(M.mods).where(M.player_id == uid, M.mods.isnot(None))
        )).scalars().all():
            seen.update(ac for ac in _mod_set(mstr) if ac not in _NOT_MASKS)
    return len(seen)

async def _calc_long_chain(session, uid) -> int:
    best = 0
    for M in (UserBestScore, UserMapAttempt):
        v = (await session.execute(
            select(func.max(M.max_combo)).where(M.player_id == uid)
        )).scalar() or 0
        best = max(best, v)
    return best

_ACCOUNT_AGE_2Y = timedelta(days=730)

def _account_age_ok(u) -> int:
    jd = getattr(u, "join_date", None)
    return 1 if jd and (utcnow() - jd) >= _ACCOUNT_AGE_2Y else 0

def _crit_calc(crit):
    async def _c(u, uid, s):
        if await _exists_best(s, uid, **crit):
            return 1
        return 1 if any(fits(play, crit) for play in (await load_history(s, uid)).local_scores) else 0
    return _c

_CALCULATORS = {code: _crit_calc(crit) for code, crit in TITLE_CRITERIA.items()}
_CALCULATORS.update({
    "registered":   lambda u, uid, s: 1 if u.osu_user_id else 0,
    "played_100k":  lambda u, uid, s: u.play_count or 0,
    "doublethink":  lambda u, uid, s: _calc_doublethink(s, uid),
    "broken_record": lambda u, uid, s: _calc_broken_record(s, uid),
    "wysi":          lambda u, uid, s: _calc_wysi(s, uid),
    "volunteer":     lambda u, uid, s: 1 if (getattr(u, "was_supporter", False) or getattr(u, "is_supporter", False)) else 0,
    "archivist":     lambda u, uid, s: _calc_archivist(s, u),
    "graveyard":     lambda u, uid, s: _calc_graveyard(s, uid),
    "archaeologist": lambda u, uid, s: _calc_archaeologist(s, uid),
    "repeat_15":      lambda u, uid, s: _calc_stuck_loop(s, uid),
    "profile_5day":   lambda u, uid, s: u.profile_opens_best or 0,
    "streak_30d":     lambda u, uid, s: u.active_streak_best or 0,
    "week_500":       lambda u, uid, s: u.week_plays_best or 0,
    "compare_50":     lambda u, uid, s: u.compare_uses or 0,
    "comeback_180d":  lambda u, uid, s: 1 if u.comeback_done else 0,
    "fail_95":   lambda u, uid, s: _calc_last_note(s, uid),
    "magic7":    lambda u, uid, s: _calc_magic7(s, uid),
    "choke_95":  lambda u, uid, s: _calc_choke(s, uid),
    "masks_5":    lambda u, uid, s: _calc_masks(s, uid),
    "combo_2000": lambda u, uid, s: _calc_long_chain(s, uid),
    "level_25":   lambda u, uid, s: u.level or 0,
    "account_2y": lambda u, uid, s: _account_age_ok(u),
    "s_50":       lambda u, uid, s: u.grade_count_s or 0,
    "ss_100":     lambda u, uid, s: u.grade_count_ss or 0,
    "big_brother": lambda u, uid, s: _calc_big_brother(s, u),
})

def _on_history(rule):
    async def _calc(u, uid, s):
        return rule(await load_history(s, uid), u)
    return _calc

_CALCULATORS.update({code: _on_history(rule) for code, rule in RULES.items()})

async def _play_unlocks(code: str, play: Dict, user: User, session) -> bool:
    crit = TITLE_CRITERIA.get(code)
    if crit is not None:
        return _play_matches(play, **crit)
    if code == "doublethink":
        if not play.get("passed"):
            return False
        if _play_matches(play, max_sr=2.0, ranks=SS_RANKS, mods_all=["EZ"]):
            return bool(await _exists_best(session, user.player_id, min_sr=7.0))
        if _play_matches(play, min_sr=7.0):
            return bool(await _exists_best(session, user.player_id, max_sr=2.0, ranks=SS_RANKS, mods_all=["EZ"]))
        return False
    if code == "repeat_15":
        run, bid = await _stuck_loop_tail(session, user.player_id)
        return run >= 15 and bid == play.get("beatmap_id")
    if code == "magic7":
        return _has_jackpot(play.get("score"))
    if code == "choke_95":
        mmc = play.get("map_max_combo")
        mc = play.get("max_combo") or 0
        acc = play.get("accuracy") or 0
        return bool(mmc) and _CHOKE_COMBO_RATIO * mmc <= mc < mmc and acc >= _CHOKE_MIN_ACC
    return False

async def evaluate_recent_plays(user: User, plays: List[Dict], session) -> List[TitleDef]:
    if not plays or user.player_id is None:
        return []
    rows = await _ensure_progress_rows(session, user.player_id)
    newly: List[TitleDef] = []
    for code, td in TITLE_REGISTRY.items():
        prog = rows[code]
        if prog.unlocked:
            continue
        unlocked_now = False
        for play in plays:
            if await _play_unlocks(code, play, user, session):
                unlocked_now = True
                break
        if not unlocked_now:
            continue
        prog.current_value = max(prog.current_value or 0, td.target)
        prog.unlocked = True
        prog.unlocked_at = utcnow()
        newly.append(td)
    return newly

async def evaluate_recent_play(user: User, play: Dict, session) -> List[TitleDef]:
    return await evaluate_recent_plays(user, [play], session)

async def _progress_rows(session, player_id: int) -> Dict[str, UserTitleProgress]:
    rows = (await session.execute(
        select(UserTitleProgress).where(UserTitleProgress.player_id == player_id)
    )).scalars().all()
    return {p.title_code: p for p in rows}

def _insert_ignoring_duplicates(session):
    bind = session.get_bind()
    name = getattr(getattr(bind, "dialect", None), "name", "")
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        return None
    return insert(UserTitleProgress)

async def _ensure_progress_rows(session, player_id: int) -> Dict[str, UserTitleProgress]:
    existing = await _progress_rows(session, player_id)
    missing = [code for code in TITLE_REGISTRY if code not in existing]
    if not missing:
        return existing
    insert = _insert_ignoring_duplicates(session)
    if insert is None:
        for code in missing:
            prog = UserTitleProgress(player_id=player_id, title_code=code,
                                     current_value=0, unlocked=False)
            session.add(prog)
            existing[code] = prog
        return existing
    await session.execute(
        insert.values([
            {"player_id": player_id, "title_code": code, "current_value": 0, "unlocked": False}
            for code in missing
        ]).on_conflict_do_nothing(index_elements=["player_id", "title_code"])
    )
    return await _progress_rows(session, player_id)

async def refresh_user_titles(user: User, session, lang: str = "en") -> List[Dict]:
    if user.player_id is None:
        await session.flush()
    if user.player_id is None:
        return []
    existing = await _ensure_progress_rows(session, user.player_id)
    forget_history(session, user.player_id)

    progress_list = []

    for code, title_def in TITLE_REGISTRY.items():
        calc = _CALCULATORS.get(code)
        if not calc:
            continue

        raw = calc(user, user.player_id, session)
        current = await raw if hasattr(raw, "__await__") else raw

        prog = existing[code]
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
            "name": title_def.name_for(lang),
            "description": title_def.description_for(lang),
            "target": title_def.target,
            "current": current,
            "progress_pct": pct,
            "unlocked": prog.unlocked,
            "unlocked_at": prog.unlocked_at,
            "color": title_def.color,
            "rarity": title_def.rarity,
            "rarity_label": title_def.rarity_label_for(lang),
            "is_active": user.active_title_code == code,
        })

    return progress_list

def build_titles_summary(progress_list: List[Dict]) -> Dict:
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
    total_stmt = (
        select(func.count(func.distinct(User.player_id)))
        .where(User.osu_user_id.isnot(None), User.player_id.isnot(None))
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
