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
    return value.strip()[:WORDS_MOST] if isinstance(value, str) else ""

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
