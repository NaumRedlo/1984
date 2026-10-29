import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select

from db.models.witnessed_play import WitnessedPlay
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("render_farm.witnessed")

EACH = timedelta(seconds=5)
DAILY = 600
KEPT_FOR = timedelta(days=14)
SAME_PLAY = timedelta(minutes=15)
CLOCK_SLACK = timedelta(minutes=5)
OLDEST = timedelta(days=14)
COUNT_MOST = 100_000
SCORE_MOST = 2_147_483_647
WORDS_MOST = 255
LEARN_WAIT = 2.5

KEPT, SEEN, BAD, TOO_SOON, TOO_MANY = "kept", "seen", "bad", "too soon", "too many"

_HASH = re.compile(r"^[0-9a-f]{32}$")
_MODS = (
    ("NF", 1), ("EZ", 2), ("TD", 4), ("HD", 8), ("HR", 16), ("SD", 32), ("DT", 64), ("RX", 128), ("HT", 256),
    ("NC", 512), ("FL", 1024), ("SO", 4096), ("AP", 8192), ("PF", 16384),
)
_SILVER = 8 | 1024
_NOT_PLAYED = 2048 | 4194304

def mods_said(bits: int) -> list[str]:
    said = [name for name, bit in _MODS if bits & bit]
    if "NC" in said and "DT" in said:
        said.remove("DT")
    if "PF" in said and "SD" in said:
        said.remove("SD")
    return said

def accuracy_of(n300: int, n100: int, n50: int, miss: int) -> float:
    total = n300 + n100 + n50 + miss
    if total <= 0:
        return 0.0
    return round((300 * n300 + 100 * n100 + 50 * n50) / (300 * total) * 100, 2)

def grade_of(n300: int, n100: int, n50: int, miss: int, bits: int, passed: bool) -> str:
    total = n300 + n100 + n50 + miss
    if not passed or total <= 0:
        return "F"
    silver = bool(bits & _SILVER)
    great = n300 / total
    if n300 == total:
        return "XH" if silver else "X"
    if great > 0.9 and n50 / total <= 0.01 and miss == 0:
        return "SH" if silver else "S"
    if (great > 0.8 and miss == 0) or great > 0.9:
        return "A"
    if (great > 0.7 and miss == 0) or great > 0.8:
        return "B"
    if great > 0.6:
        return "C"
    return "D"

def _count(said: dict, key: str, most: int = COUNT_MOST) -> Optional[int]:
    value = said.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > most:
        return None
    return value

def _words(said: dict, key: str) -> str:
    value = said.get(key)
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable() or c == " ").strip()[:WORDS_MOST]

STATUSES = ("ranked", "approved", "qualified", "loved", "pending", "unsubmitted")
STARS_MOST = 30.0
BPM_MOST = 2000.0
LENGTH_MOST = 86_400

def _number(said: Any, most: float) -> Optional[float]:
    if isinstance(said, bool) or not isinstance(said, (int, float)):
        return None
    value = float(said)
    return value if 0.0 < value <= most else None

def facts_told(said: Any) -> Optional[dict[str, Any]]:
    if not isinstance(said, dict):
        return None
    stars = _number(said.get("stars"), STARS_MOST)
    bpm = _number(said.get("bpm"), BPM_MOST)
    length = _number(said.get("length"), LENGTH_MOST)
    status = said.get("status")
    told = {
        "stars": round(stars, 2) if stars is not None else None,
        "bpm": round(bpm, 2) if bpm is not None else None,
        "length": int(length) if length is not None else None,
        "status": status if status in STATUSES else None,
    }
    return told if any(value is not None for value in told.values()) else None

def read(said: dict, now: datetime) -> Optional[dict[str, Any]]:
    md5 = str(said.get("md5") or "").lower()
    replay = str(said.get("replay") or "").lower()
    if not _HASH.match(md5) or not _HASH.match(replay):
        return None
    numbers = {key: _count(said, key) for key in ("n300", "n100", "n50", "geki", "katu", "miss", "max_combo")}
    score = _count(said, "score", SCORE_MOST)
    bits = _count(said, "mods", SCORE_MOST)
    beatmap = _count(said, "id", SCORE_MOST)
    beatmapset = _count(said, "set", SCORE_MOST)
    if score is None or bits is None or bits & _NOT_PLAYED or any(value is None for value in numbers.values()):
        return None
    if numbers["n300"] + numbers["n100"] + numbers["n50"] + numbers["miss"] <= 0:
        return None
    ended = said.get("ended")
    played = now
    if isinstance(ended, int) and not isinstance(ended, bool):
        try:
            told = datetime.fromtimestamp(ended, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            told = now
        if told < now - OLDEST:
            return None
        if now - OLDEST <= told <= now + CLOCK_SLACK:
            played = min(told, now)
    return {
        "md5": md5, "replay": replay, "score": score, "bits": bits, "beatmap": beatmap or None, "set": beatmapset or None,
        "passed": said.get("passed") is True, "played": played,
        "artist": _words(said, "artist"), "title": _words(said, "title"), "version": _words(said, "version"), "creator": _words(said, "creator"),
        "facts": facts_told(said.get("facts")),
        **numbers,
    }

async def _facts(osu, beatmap_id: Optional[int], md5: str) -> Optional[dict]:
    if osu is None or not beatmap_id:
        return None
    from services.leaderboard.service import beatmap_facts

    known = await beatmap_facts(osu, beatmap_id)
    if not known or str(known.get("checksum") or "").lower() != md5:
        return None
    return known

async def _judged(row: WitnessedPlay) -> Optional[dict]:
    from utils.osu import assay_service

    if not assay_service.enabled() or not row.beatmap_id:
        return None
    try:
        return await assay_service.score(
            int(row.beatmap_id),
            mods=[*(part for part in (row.mods or "").split(",") if part), "CL"],
            statistics={"great": row.count_300, "ok": row.count_100, "meh": row.count_50, "miss": row.count_miss},
            checksum=row.beatmap_md5,
            accuracy=float(row.accuracy or 0.0) / 100,
            max_combo=row.max_combo,
            legacy_total_score=int(row.score or 0) or None,
            is_legacy=True,
        )
    except Exception as exc:
        logger.info("a witnessed play was not judged: %s", exc)
        return None

async def keep(session, player_id: int, said: dict, *, now: Optional[datetime] = None) -> tuple[str, Optional[WitnessedPlay]]:
    now = now or utcnow()
    told = read(said, now)
    if told is None:
        return BAD, None
    seen = (await session.execute(
        select(WitnessedPlay).where(WitnessedPlay.player_id == player_id, WitnessedPlay.replay_hash == told["replay"])
    )).scalar_one_or_none()
    if seen is not None:
        return SEEN, seen
    newest = (await session.execute(select(func.max(WitnessedPlay.created_at)).where(WitnessedPlay.player_id == player_id))).scalar()
    if newest is not None and now - newest.replace(tzinfo=None) < EACH:
        return TOO_SOON, None
    today = (await session.execute(
        select(func.count(WitnessedPlay.id)).where(WitnessedPlay.player_id == player_id, WitnessedPlay.created_at >= now - timedelta(days=1))
    )).scalar() or 0
    if today >= DAILY:
        return TOO_MANY, None
    row = WitnessedPlay(
        player_id=player_id,
        replay_hash=told["replay"],
        beatmap_md5=told["md5"],
        beatmap_id=told["beatmap"],
        beatmapset_id=told["set"],
        artist=told["artist"],
        title=told["title"],
        version=told["version"],
        creator=told["creator"],
        mods=",".join(mods_said(told["bits"])),
        score=told["score"],
        accuracy=accuracy_of(told["n300"], told["n100"], told["n50"], told["miss"]),
        max_combo=told["max_combo"],
        count_300=told["n300"], count_100=told["n100"], count_50=told["n50"],
        count_geki=told["geki"], count_katu=told["katu"], count_miss=told["miss"],
        rank=grade_of(told["n300"], told["n100"], told["n50"], told["miss"], told["bits"], told["passed"]),
        passed=told["passed"],
        played_at=told["played"],
        created_at=now,
    )
    known = told["facts"] or {}
    row.star_rating, row.bpm, row.length, row.status = known.get("stars"), known.get("bpm"), known.get("length"), known.get("status")
    session.add(row)
    await session.execute(delete(WitnessedPlay).where(WitnessedPlay.player_id == player_id, WitnessedPlay.created_at < now - KEPT_FOR))
    await session.commit()
    return KEPT, row

async def learn(session, osu, play_id: int) -> bool:
    row = await session.get(WitnessedPlay, play_id)
    if row is None:
        return False
    known = await _facts(osu, row.beatmap_id, row.beatmap_md5)
    if known is None:
        return False
    beatmapset = known.get("beatmapset") or {}
    stars, bpm = known.get("difficulty_rating"), known.get("bpm")
    row.beatmapset_id = beatmapset.get("id") or row.beatmapset_id
    row.artist = beatmapset.get("artist") or row.artist
    row.title = beatmapset.get("title") or row.title
    row.version = known.get("version") or row.version
    row.creator = beatmapset.get("creator") or row.creator
    row.star_rating = round(float(stars), 2) if stars is not None else None
    row.bpm = float(bpm) if bpm is not None else None
    row.length = known.get("total_length")
    row.map_max_combo = known.get("max_combo")
    row.status = known.get("status")
    judged = await _judged(row)
    if judged:
        if judged.get("star_rating") is not None:
            row.star_rating = round(float(judged["star_rating"]), 2)
        most = (judged.get("map") or {}).get("max_combo")
        if most:
            row.map_max_combo = int(most)
        if row.passed and judged.get("pp") is not None:
            row.pp_estimated = round(float(judged["pp"]), 2)
    await session.commit()
    return True

_learning: set = set()

async def _learnt(factory, osu, play_id: int) -> None:
    try:
        async with factory() as session:
            await learn(session, osu, play_id)
    except Exception as exc:
        logger.info("the map of a witnessed play was not learnt: %s", exc)

async def learn_soon(factory, osu, play_id: int, *, wait: float = LEARN_WAIT) -> None:
    task = asyncio.create_task(_learnt(factory, osu, play_id))
    _learning.add(task)
    task.add_done_callback(_learning.discard)
    try:
        await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.TimeoutError:
        pass

def same_play(row: WitnessedPlay, attempt) -> bool:
    if attempt.player_id != row.player_id:
        return False
    if row.beatmap_id and attempt.beatmap_id and attempt.beatmap_id != row.beatmap_id:
        return False
    when = attempt.played_at if attempt.played_at is not None else attempt.created_at
    if when is None or abs(when.replace(tzinfo=None) - row.played_at.replace(tzinfo=None)) > SAME_PLAY:
        return False
    if row.score and int(attempt.score or 0) == int(row.score):
        return True
    return (attempt.count_100, attempt.count_50, attempt.count_miss, attempt.max_combo) == (row.count_100, row.count_50, row.count_miss, row.max_combo)

async def unconfirmed(session, players: list[int], attempts: list, *, most: int, now: Optional[datetime] = None) -> list[WitnessedPlay]:
    if not players:
        return []
    now = now or utcnow()
    since = now - KEPT_FOR
    if len(attempts) >= most:
        since = max(since, min(attempt.played_at.replace(tzinfo=None) for attempt in attempts if attempt.played_at is not None))
    rows = (await session.execute(
        select(WitnessedPlay)
        .where(WitnessedPlay.player_id.in_(players), WitnessedPlay.played_at >= since)
        .order_by(WitnessedPlay.played_at.desc())
        .limit(most)
    )).scalars().all()
    return [row for row in rows if not any(same_play(row, attempt) for attempt in attempts)]

def said(row: WitnessedPlay) -> dict[str, Any]:
    from services.render_farm.community import mods_of, grade_of as shown_grade, stamp

    return {
        "id": None,
        "witnessed": True,
        "map": {
            "beatmap": row.beatmap_id,
            "set": row.beatmapset_id,
            "artist": row.artist or "",
            "title": row.title or "",
            "version": row.version or "",
            "creator": row.creator or "",
            "stars": round(float(row.star_rating), 2) if row.star_rating is not None else None,
        },
        "score": int(row.score or 0),
        "pp": 0.0,
        "pp_if": row.pp_estimated,
        "accuracy": round(float(row.accuracy or 0.0), 2),
        "mods": mods_of(row.mods),
        "grade": shown_grade(row.rank),
        "combo": row.max_combo,
        "max_combo": row.map_max_combo,
        "full_combo": bool(row.passed and row.count_miss == 0 and row.map_max_combo and row.max_combo >= row.map_max_combo - 10),
        "counts": [row.count_300, row.count_100, row.count_50, row.count_miss],
        "at": stamp(row.played_at),
    }


def day_bounds(zone: str, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        place = ZoneInfo(zone) if zone else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        place = timezone.utc
    local = (now or utcnow()).replace(tzinfo=timezone.utc).astimezone(place)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None), (start + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)

async def day(session, player_id: int, start: datetime, end: datetime) -> dict[str, Any]:
    from db.models.best_score import UserBestScore as BestScore
    from db.models.witness_session import WitnessSession
    from services.render_farm.community import mods_of

    plays = (await session.scalars(select(WitnessedPlay).where(WitnessedPlay.player_id == player_id, WitnessedPlay.played_at >= start,
                                                             WitnessedPlay.played_at < end))).all()
    seconds = await session.scalar(select(func.coalesce(func.sum(WitnessSession.play_seconds), 0)).where(
        WitnessSession.player_id == player_id, WitnessSession.ended_at >= start, WitnessSession.started_at < end))
    records = await session.scalar(select(func.count(BestScore.id)).where(BestScore.player_id == player_id, BestScore.created_at >= start,
                                                                         BestScore.created_at < end))
    best = max((row for row in plays if row.passed and row.pp_estimated is not None), key=lambda row: row.pp_estimated, default=None)
    return {"plays": len(plays), "seconds": int(seconds or 0), "records": int(records or 0),
            "best": {"title": " ".join(part for part in (best.title or "", f"[{best.version}]" if best.version else "") if part),
                     "pp": round(float(best.pp_estimated), 2), "accuracy": round(float(best.accuracy or 0.0), 2),
                     "mods": "".join(mods_of(best.mods)), "stars": round(float(best.star_rating or 0.0), 2)} if best is not None else None}

SAME_PLAY = timedelta(minutes=10)

def _stamp_of(said: Any) -> Optional[datetime]:
    if not isinstance(said, str):
        return None
    try:
        return datetime.fromisoformat(said.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None

def matching(row: WitnessedPlay, recent: list[dict]) -> Optional[dict]:
    near = []
    for score in recent:
        beatmap = (score.get("beatmap") or {}).get("id") or score.get("beatmap_id")
        at = _stamp_of(score.get("ended_at") or score.get("created_at"))
        if row.beatmap_id and beatmap == row.beatmap_id and at is not None and abs(at - row.played_at) <= SAME_PLAY:
            near.append((abs(at - row.played_at), score))
    return min(near, key=lambda pair: pair[0])[1] if near else None

def raw_score_of(row: WitnessedPlay, beatmap: Optional[dict]) -> dict[str, Any]:
    from services.render_farm.community import mods_of

    found = dict(beatmap or {})
    beatmapset = dict(found.pop("beatmapset", None) or {})
    found = {"id": row.beatmap_id or 0, "version": row.version or "", "difficulty_rating": row.star_rating or 0.0, "bpm": row.bpm or 0.0,
             "total_length": row.length or 0, "max_combo": row.map_max_combo or 0, "checksum": row.beatmap_md5, **found}
    beatmapset = {"id": row.beatmapset_id or 0, "artist": row.artist or "", "title": row.title or "", "creator": row.creator or "", **beatmapset}
    return {"id": 0, "beatmap": found, "beatmapset": beatmapset, "accuracy": float(row.accuracy or 0.0) / 100, "passed": bool(row.passed),
            "rank": row.rank or "F", "pp": row.pp_estimated, "max_combo": row.max_combo or 0, "ruleset_id": 0,
            "mods": [{"acronym": mod} for mod in mods_of(row.mods)], "ended_at": row.played_at.isoformat() + "Z",
            "statistics": {"great": row.count_300 or 0, "ok": row.count_100 or 0, "meh": row.count_50 or 0, "miss": row.count_miss or 0}}

async def card_score(osu, osu_user_id: Optional[int], row: WitnessedPlay) -> dict[str, Any]:
    if osu is not None and osu_user_id:
        try:
            found = matching(row, await osu.get_user_recent_scores(int(osu_user_id), limit=50, mode="osu"))
        except Exception:
            found = None
        if found is not None:
            return found
    beatmap = None
    if osu is not None and row.beatmap_id:
        try:
            beatmap = await osu.get_beatmap(row.beatmap_id)
        except Exception:
            beatmap = None
    return raw_score_of(row, beatmap)
