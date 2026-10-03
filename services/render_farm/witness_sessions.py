import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from db.models.witness_session import WitnessSession
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("render_farm.witness_sessions")

KEPT, BAD = "kept", "bad"

LONGEST = timedelta(hours=36)
OLDEST = timedelta(days=14)
CLOCK_SLACK = timedelta(minutes=5)
PLAYS_MOST = 5000
ZONE_WORDS = re.compile(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}$")

def zone_of(name: Any) -> Optional[str]:
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or len(name) > 64 or not ZONE_WORDS.match(name):
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return name

def _moment(said: Any) -> Optional[datetime]:
    if isinstance(said, bool) or not isinstance(said, int) or said < 0 or said > 4_102_444_800:
        return None
    return datetime.fromtimestamp(said, tz=timezone.utc).replace(tzinfo=None)

def _whole(said: Any, most: int) -> Optional[int]:
    if isinstance(said, bool) or not isinstance(said, int) or said < 0 or said > most:
        return None
    return said

def read(said: dict, now: datetime) -> Optional[dict[str, Any]]:
    started, ended = _moment(said.get("started_at")), _moment(said.get("ended_at"))
    if started is None or ended is None or ended < started:
        return None
    if ended - started > LONGEST or ended > now + CLOCK_SLACK or started < now - OLDEST - LONGEST:
        return None
    wall = int((ended - started).total_seconds())
    played = _whole(said.get("play_seconds", 0), wall + 60)
    plays = _whole(said.get("plays", 0), PLAYS_MOST)
    if played is None or plays is None:
        return None
    return {"started_at": started, "ended_at": ended, "play_seconds": min(played, wall), "plays": plays}

async def keep(session, player, said: dict, now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    zone = zone_of(said.get("time_zone"))
    told_span = said.get("started_at") is not None
    span = read(said, now) if told_span else None
    if (told_span and span is None) or (zone is None and span is None):
        return BAD
    if zone is not None and player.time_zone != zone:
        player.time_zone = zone
    if span is None:
        return KEPT
    row = (await session.execute(
        select(WitnessSession).where(WitnessSession.player_id == player.id, WitnessSession.started_at == span["started_at"])
    )).scalar_one_or_none()
    if row is None:
        session.add(WitnessSession(player_id=player.id, **span))
        return KEPT
    row.ended_at = max(row.ended_at, span["ended_at"])
    row.play_seconds = max(row.play_seconds or 0, span["play_seconds"])
    row.plays = max(row.plays or 0, span["plays"])
    return KEPT
