from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from services.leaderboard.service import (_map_record_history, _ranks_by_score, _sync_beatmap_scores, beatmap_facts,
                                          known_beatmap)
from services.render_farm.community import _play, naive, stamp
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("render_farm.maps")

RECORDS = 6

def _worth(row, by_score: bool) -> float:
    if by_score:
        return float(row.score or 0)
    return float(row.pp or row.pp_estimated or 0)

def _when(row) -> Optional[datetime]:
    return row.played_at if row.played_at is not None else row.created_at

def _newest(rows, name: str):
    return next((getattr(row, name) for row in rows if getattr(row, name) not in (None, "", 0)), None)

def _about(beatmap_id: int, known: Optional[dict], attempts, status: Optional[str]) -> Optional[dict[str, Any]]:
    if known:
        beatmapset = known.get("beatmapset") or {}
        stars, bpm = known.get("difficulty_rating"), known.get("bpm")
        return {
            "beatmap": beatmap_id,
            "set": beatmapset.get("id") or known.get("beatmapset_id"),
            "artist": beatmapset.get("artist") or "",
            "title": beatmapset.get("title") or "",
            "version": known.get("version") or "",
            "creator": beatmapset.get("creator") or "",
            "stars": round(float(stars), 2) if stars is not None else None,
            "bpm": round(float(bpm), 1) if bpm is not None else None,
            "length": known.get("total_length"),
            "max_combo": known.get("max_combo"),
            "status": status or "",
        }
    if not attempts:
        return None
    rows = sorted(attempts, key=lambda row: (naive(_when(row)) or datetime.min, row.id), reverse=True)
    stars, bpm = _newest(rows, "star_rating"), _newest(rows, "bpm")
    return {
        "beatmap": beatmap_id,
        "set": _newest(rows, "beatmapset_id"),
        "artist": _newest(rows, "artist") or "",
        "title": _newest(rows, "title") or "",
        "version": _newest(rows, "version") or "",
        "creator": _newest(rows, "creator") or "",
        "stars": round(float(stars), 2) if stars is not None else None,
        "bpm": round(float(bpm), 1) if bpm is not None else None,
        "length": _newest(rows, "length"),
        "max_combo": _newest(rows, "map_max_combo"),
        "status": status or "",
    }

async def board(session, osu, beatmap_id: int, chat_id: int, viewer: int, *, sync: bool = True,
                now: Optional[datetime] = None) -> dict[str, Any]:
    if sync and osu is not None:
        try:
            await _sync_beatmap_scores(session, osu, beatmap_id, chat_id)
        except Exception as exc:
            logger.info("scores of map %s were not refreshed: %s", beatmap_id, exc)

    users = {
        user.player_id: user for user in (await session.execute(
            select(User).where(User.chat_id == chat_id, User.osu_user_id.isnot(None), User.player_id.isnot(None))
        )).scalars().all()
    }
    attempts = (await session.execute(
        select(UserMapAttempt)
        .where(UserMapAttempt.player_id.in_(list(users)), UserMapAttempt.beatmap_id == beatmap_id)
        .order_by(UserMapAttempt.id)
    )).scalars().all() if users else []

    known = known_beatmap(beatmap_id)
    if known is None and sync and osu is not None and attempts:
        known = await beatmap_facts(osu, beatmap_id)
    status = (known or {}).get("status") or next((row.status for row in sorted(attempts, key=lambda row: row.id, reverse=True) if row.status), None)
    by_score = _ranks_by_score(status)

    best: dict[int, Any] = {}
    for row in attempts:
        if row.passed is False:
            continue
        held = best.get(row.player_id)
        if held is None or _worth(row, by_score) > _worth(held, by_score):
            best[row.player_id] = row
    ranked = sorted(best.values(), key=lambda row: (-_worth(row, by_score), row.id))

    rows = []
    for place, row in enumerate(ranked, 1):
        user = users[row.player_id]
        rows.append({
            "who": user.id,
            "player": user.player_id,
            "name": user.osu_username,
            "country": (user.country or "").upper(),
            "avatar": user.avatar_url or (f"https://a.ppy.sh/{user.osu_user_id}" if user.osu_user_id else ""),
            "place": place,
            "you": user.telegram_id == viewer,
            **_play(row, when=_when(row)),
        })

    records = [{
        "name": record["username"],
        "pp": round(float(record["pp"] or 0.0), 2),
        "score": int(record["score"] or 0),
        "at": stamp(record["at"]),
    } for record in await _map_record_history(session, beatmap_id, chat_id, rank_by_score=by_score, limit=RECORDS)] if attempts else []

    return {
        "beatmap": beatmap_id,
        "chat": chat_id,
        "metric": "score" if by_score else "pp",
        "map": _about(beatmap_id, known, attempts, status) if attempts else None,
        "plays": len(attempts),
        "players": len({row.player_id for row in attempts}),
        "rows": rows,
        "records": records,
        "at": stamp(naive(now) or utcnow()),
    }
