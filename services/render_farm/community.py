from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select

from db.models.best_score import UserBestScore
from db.models.leaderboard_snapshot import LeaderboardSnapshot
from db.models.map_attempt import UserMapAttempt
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from services.leaderboard.deltas import DELTA_CATEGORIES, absolute_for, delta_for
from services.leaderboard.periods import current_period_key, period_start_utc, week_number
from utils.timeutils import utcnow
from utils.titles import RARITY_ORDER, TITLE_REGISTRY

TOP = 5
RECENT = 12
LIVE = 40
HAPPENED = timedelta(days=14)
BASELINE_GRACE = timedelta(minutes=10)

def stamp(moment: Optional[datetime]) -> Optional[int]:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())

def naive(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None or moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)

def mods_of(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [part.strip().upper() for part in str(raw).split(",") if part.strip() and part.strip().upper() not in ("CL", "NM")]

def grade_of(raw: Optional[str]) -> str:
    said = (raw or "").upper()
    return {"X": "SS", "XH": "SS", "SH": "S"}.get(said, said or "F")

def _map(row) -> dict[str, Any]:
    return {
        "beatmap": row.beatmap_id,
        "set": row.beatmapset_id,
        "artist": row.artist or "",
        "title": row.title or "",
        "version": row.version or "",
        "creator": row.creator or "",
        "stars": round(float(row.star_rating), 2) if row.star_rating is not None else None,
    }

def _play(row, *, when: Optional[datetime] = None) -> dict[str, Any]:
    return {
        "map": _map(row),
        "pp": round(float(row.pp or 0.0), 2),
        "accuracy": round(float(row.accuracy or 0.0), 2),
        "mods": mods_of(row.mods),
        "grade": grade_of(row.rank),
        "combo": row.max_combo,
        "max_combo": row.map_max_combo,
        "full_combo": bool(row.is_fc),
        "at": stamp(when if when is not None else row.created_at),
    }

def titles_catalogue() -> list[dict[str, Any]]:
    ordered = sorted(TITLE_REGISTRY.values(), key=lambda t: (RARITY_ORDER.index(t.rarity), t.code))
    return [{
        "code": t.code,
        "rarity": t.rarity,
        "name": t.name,
        "name_ru": t.name_ru or t.name,
        "about": t.description,
        "about_ru": t.description_ru or t.description,
        "target": t.target,
    } for t in ordered]

def _positions(values: dict[int, float]) -> dict[int, int]:
    ranked = sorted((uid for uid, value in values.items() if value > 0), key=lambda uid: values[uid], reverse=True)
    return {uid: place for place, uid in enumerate(ranked, 1)}

def _movement(users: list, anchors: dict) -> tuple[dict[int, list[int]], dict[int, list[float]]]:
    moved: dict[int, list[int]] = {u.id: [0] * len(DELTA_CATEGORIES) for u in users}
    gained: dict[int, list[float]] = {u.id: [0.0] * len(DELTA_CATEGORIES) for u in users}
    for at, key in enumerate(DELTA_CATEGORIES):
        now = _positions({u.id: absolute_for(u, key) for u in users})
        then = _positions({u.id: absolute_for(anchors.get(u.id, u), key) for u in users})
        for u in users:
            if u.id in now and u.id in then and u.id in anchors:
                moved[u.id][at] = then[u.id] - now[u.id]
            grown = delta_for(u, anchors.get(u.id), key)
            gained[u.id][at] = round(float(grown), 2) if grown else 0.0
    return moved, gained

def _hours(user) -> int:
    return int((user.play_time or 0) // 3600)

def _hits_per_play(user) -> float:
    plays = user.play_count or 0
    return round((user.total_hits or 0) / plays, 1) if plays else 0.0

def person(user, *, titles: Iterable[str], top: list, moved: list[int], gained: list[float], you: bool) -> dict[str, Any]:
    return {
        "id": user.id,
        "osu_id": user.osu_user_id,
        "name": user.osu_username,
        "country": (user.country or "").upper(),
        "pp": int(user.player_pp or 0),
        "rank": int(user.global_rank or 0),
        "accuracy": round(float(user.accuracy or 0.0), 2),
        "plays": int(user.play_count or 0),
        "hours": _hours(user),
        "score": int(user.ranked_score or 0),
        "hits_per_play": _hits_per_play(user),
        "level": int(user.level or 0),
        "ss": int(user.grade_count_ss or 0),
        "s": int(user.grade_count_s or 0),
        "joined": stamp(user.join_date),
        "supporter": bool(user.is_supporter),
        "title": user.active_title_code,
        "titles": sorted(titles),
        "streak": int(user.active_streak or 0),
        "streak_best": int(user.active_streak_best or 0),
        "avatar": user.avatar_url or (f"https://a.ppy.sh/{user.osu_user_id}" if user.osu_user_id else ""),
        "cover": user.cover_url or "",
        "moved": moved,
        "gained": gained,
        "top": top,
        "you": you,
    }

async def gather(session, chat_id: int, viewer: int, *, now: Optional[datetime] = None) -> dict[str, Any]:
    now = naive(now) or utcnow()
    period = current_period_key(now)
    users = (await session.execute(
        select(User).where(User.chat_id == chat_id, User.osu_user_id.isnot(None))
    )).scalars().all()
    ids = [u.id for u in users]
    by_id = {u.id: u for u in users}

    anchors = {
        s.user_id: s for s in (await session.execute(
            select(LeaderboardSnapshot).where(
                LeaderboardSnapshot.tenant_chat_id == chat_id,
                LeaderboardSnapshot.period_key == period,
            )
        )).scalars().all()
    } if ids else {}
    moved, gained = _movement(users, anchors)

    unlocked: dict[int, list] = {uid: [] for uid in ids}
    if ids:
        for row in (await session.execute(
            select(UserTitleProgress).where(UserTitleProgress.user_id.in_(ids), UserTitleProgress.unlocked.is_(True))
        )).scalars().all():
            if row.title_code in TITLE_REGISTRY:
                unlocked[row.user_id].append(row)

    best: dict[int, list] = {uid: [] for uid in ids}
    if ids:
        for row in (await session.execute(
            select(UserBestScore).where(UserBestScore.user_id.in_(ids)).order_by(UserBestScore.user_id, UserBestScore.pp.desc())
        )).scalars().all():
            best[row.user_id].append(row)

    people = []
    for u in users:
        people.append(person(
            u,
            titles=[row.title_code for row in unlocked[u.id]],
            top=[_play(row) for row in best[u.id][:TOP]],
            moved=moved[u.id],
            gained=gained[u.id],
            you=u.telegram_id == viewer,
        ))
    people.sort(key=lambda p: p["pp"], reverse=True)

    live = []
    if ids:
        for row in (await session.execute(
            select(UserMapAttempt)
            .where(UserMapAttempt.user_id.in_(ids), UserMapAttempt.played_at.isnot(None))
            .order_by(UserMapAttempt.played_at.desc())
            .limit(LIVE)
        )).scalars().all():
            live.append({"who": row.user_id, "passed": row.passed is not False, **_play(row, when=row.played_at)})

    happened = []
    since = now - HAPPENED
    for uid, rows in best.items():
        baseline = naive(by_id[uid].best_scores_baseline_at)
        if baseline is None:
            continue
        for place, row in enumerate(rows, 1):
            made = naive(row.created_at)
            if made and made >= since and made > baseline + BASELINE_GRACE:
                happened.append({"who": uid, "kind": "top_play", "place": place, "at": stamp(row.created_at), "play": _play(row)})
    for uid, rows in unlocked.items():
        for row in rows:
            opened = naive(row.unlocked_at)
            if opened and opened >= since and row.title_code != "registered":
                happened.append({"who": uid, "kind": "title", "title": row.title_code, "at": stamp(row.unlocked_at)})
    week_began = stamp(period_start_utc(period))
    for p in people:
        climbed = p["moved"][0]
        if climbed > 0:
            place = 1 + sum(1 for other in people if other["pp"] > p["pp"])
            happened.append({"who": p["id"], "kind": "climb", "board": DELTA_CATEGORIES[0], "from": place + climbed, "to": place, "at": week_began})
    happened.sort(key=lambda h: h["at"] or 0, reverse=True)

    return {
        "chat": chat_id,
        "week": week_number(period),
        "people": people,
        "live": live,
        "happened": happened[:60],
        "titles": titles_catalogue(),
        "at": stamp(now),
    }

async def chosen(session, viewer: int, chat_id: Optional[int] = None):
    rows = (await session.execute(
        select(User).where(User.telegram_id == viewer, User.osu_user_id.isnot(None))
    )).scalars().all()
    if not rows:
        return None
    picked = next((u for u in rows if u.chat_id == chat_id), None) if chat_id is not None else None
    return picked or max(rows, key=lambda u: naive(u.last_api_update or u.updated_at) or datetime.min)

async def own(session, viewer: int, chat_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    mine = await chosen(session, viewer, chat_id)
    if mine is None:
        return None
    titles = [
        row.title_code for row in (await session.execute(
            select(UserTitleProgress).where(UserTitleProgress.user_id == mine.id, UserTitleProgress.unlocked.is_(True))
        )).scalars().all()
        if row.title_code in TITLE_REGISTRY
    ]
    top = (await session.execute(
        select(UserBestScore).where(UserBestScore.user_id == mine.id).order_by(UserBestScore.pp.desc()).limit(TOP)
    )).scalars().all()
    recent = (await session.execute(
        select(UserMapAttempt)
        .where(UserMapAttempt.user_id == mine.id, UserMapAttempt.played_at.isnot(None))
        .order_by(UserMapAttempt.played_at.desc())
        .limit(RECENT)
    )).scalars().all()
    body = person(
        mine,
        titles=titles,
        top=[_play(row) for row in top],
        moved=[0] * len(DELTA_CATEGORIES),
        gained=[0.0] * len(DELTA_CATEGORIES),
        you=True,
    )
    body["recent"] = [{"passed": row.passed is not False, **_play(row, when=row.played_at)} for row in recent]
    body["duels"] = [int(mine.duel_wins or 0), int(mine.duel_losses or 0)]
    body["points"] = int(mine.hps_points or 0)
    return body

def friends_from(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("friends") or raw.get("users") or []
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        user = item.get("target") if isinstance(item.get("target"), dict) else item
        stats = user.get("statistics") or {}
        rulesets = user.get("statistics_rulesets") or {}
        if not stats and isinstance(rulesets.get("osu"), dict):
            stats = rulesets["osu"]
        seen = user.get("last_visit")
        seen_at = None
        if isinstance(seen, str):
            try:
                seen_at = stamp(datetime.fromisoformat(seen.replace("Z", "+00:00")))
            except ValueError:
                seen_at = None
        country = user.get("country_code") or (user.get("country") or {}).get("code") or ""
        out.append({
            "osu_id": user.get("id"),
            "name": user.get("username") or "",
            "country": str(country).upper(),
            "pp": int(stats.get("pp") or 0),
            "rank": int(stats.get("global_rank") or 0),
            "online": bool(user.get("is_online")),
            "seen": seen_at,
            "avatar": user.get("avatar_url") or "",
            "mutual": bool(item.get("mutual")) if "target" in item else False,
        })
    out.sort(key=lambda f: (not f["online"], -(f["seen"] or 0), f["name"].lower()))
    return out
