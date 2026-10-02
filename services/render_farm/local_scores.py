from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from db.models.local_score import LocalScore
from services.render_farm.witnessed import STATUSES
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("render_farm.local_scores")

KEPT, BAD, TOO_MANY = "kept", "bad", "too many"

ROWS_MOST = 1000
PLAYER_MOST = 300_000
EARLIEST = 1_167_609_600
CLOCK_SLACK = timedelta(days=1)
COUNT_MOST = 100_000
SCORE_MOST = 2_147_483_647
ID_MOST = 2_147_483_647
OBJECTS_MOST = 200_000
LENGTH_MOST = 86_400
NOT_PLAYED = 2048 | 4_194_304

def _whole(said: Any, most: int, least: int = 0) -> Optional[int]:
    if isinstance(said, bool) or not isinstance(said, int) or said < least or said > most:
        return None
    return said

def _decimal(said: Any, most: float) -> Optional[float]:
    if isinstance(said, bool) or not isinstance(said, (int, float)):
        return None
    value = float(said)
    return round(value, 3) if 0.0 < value <= most else None

def _hash(said: Any) -> Optional[str]:
    text = str(said or "").lower()
    return text if len(text) == 32 and all(c in "0123456789abcdef" for c in text) else None

def read(said: Any, now: datetime) -> Optional[dict[str, Any]]:
    if not isinstance(said, dict):
        return None
    md5 = _hash(said.get("md5"))
    beatmap = _whole(said.get("id"), ID_MOST, 1)
    played = _whole(said.get("played"), 4_102_444_800, EARLIEST)
    score = _whole(said.get("score"), SCORE_MOST)
    combo = _whole(said.get("combo"), COUNT_MOST)
    mods = _whole(said.get("mods"), SCORE_MOST)
    counts = {key: _whole(said.get(key), COUNT_MOST) for key in ("n300", "n100", "n50", "geki", "katu", "miss")}
    if None in (md5, beatmap, played, score, combo, mods) or any(value is None for value in counts.values()):
        return None
    if mods & NOT_PLAYED or counts["n300"] + counts["n100"] + counts["n50"] + counts["miss"] <= 0:
        return None
    moment = datetime.fromtimestamp(played, tz=timezone.utc).replace(tzinfo=None)
    if moment > now + CLOCK_SLACK:
        return None
    status = said.get("status")
    return {
        "beatmap_md5": md5, "beatmap_id": beatmap, "beatmapset_id": _whole(said.get("set"), ID_MOST, 1), "played_at": moment,
        "score": score, "max_combo": combo, "count_300": counts["n300"], "count_100": counts["n100"], "count_50": counts["n50"],
        "count_geki": counts["geki"], "count_katu": counts["katu"], "count_miss": counts["miss"],
        "perfect": said.get("perfect") is True, "mods": mods,
        "star_rating": _decimal(said.get("stars"), 30.0), "base_star_rating": _decimal(said.get("base_stars"), 30.0),
        "ar": _decimal(said.get("ar"), 12.0), "cs": _decimal(said.get("cs"), 12.0), "od": _decimal(said.get("od"), 12.0), "hp": _decimal(said.get("hp"), 12.0),
        "bpm": _decimal(said.get("bpm"), 2000.0), "length": _whole(said.get("length"), LENGTH_MOST), "objects": _whole(said.get("objects"), OBJECTS_MOST),
        "status": status if status in STATUSES else None,
    }

def _insert(session):
    name = getattr(getattr(session.get_bind(), "dialect", None), "name", "")
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        return None
    return insert(LocalScore)

async def _count(session, player_id: int) -> int:
    return (await session.execute(select(func.count(LocalScore.id)).where(LocalScore.player_id == player_id))).scalar() or 0

async def keep(session, player_id: int, said: Any, *, now: Optional[datetime] = None) -> tuple[str, int]:
    now = now or utcnow()
    rows = said.get("scores") if isinstance(said, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > ROWS_MOST:
        return BAD, 0
    read_rows = [row for row in (read(one, now) for one in rows) if row is not None]
    if not read_rows:
        return BAD, 0
    before = await _count(session, player_id)
    if before >= PLAYER_MOST:
        return TOO_MANY, 0
    insert = _insert(session)
    if insert is not None:
        await session.execute(
            insert.values([{**row, "player_id": player_id, "created_at": now} for row in read_rows])
            .on_conflict_do_nothing(index_elements=["player_id", "beatmap_md5", "played_at", "score"])
        )
    else:
        known = {
            (row.beatmap_md5, row.played_at, row.score)
            for row in (await session.execute(select(LocalScore).where(LocalScore.player_id == player_id))).scalars().all()
        }
        for row in read_rows:
            if (row["beatmap_md5"], row["played_at"], row["score"]) not in known:
                session.add(LocalScore(player_id=player_id, created_at=now, **row))
    await session.commit()
    return KEPT, await _count(session, player_id) - before
