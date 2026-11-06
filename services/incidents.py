import json
import secrets
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import delete, select

from db.database import AsyncSessionFactory
from db.models.incident import Incident
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("services.incidents")

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
KEEP = timedelta(days=30)
REMEMBERED = 200
TRACE_MOST = 12000
DETAIL_MOST = 4000

saving = True

@dataclass
class Noted:
    code: str
    area: str
    stage: str
    reason: str
    telegram_id: Optional[int] = None
    chat_id: Optional[int] = None
    detail: Optional[str] = None
    trace: Optional[str] = None
    created_at: datetime = field(default_factory=utcnow)

_recent: deque[Noted] = deque(maxlen=REMEMBERED)

def new_code() -> str:
    return "".join(secrets.choice(LETTERS) for _ in range(CODE_LENGTH))

def _detail(detail: Optional[dict[str, Any]]) -> Optional[str]:
    if not detail:
        return None
    try:
        written = json.dumps(detail, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        written = repr(detail)
    return written[:DETAIL_MOST]

def _trace(error: Optional[BaseException]) -> Optional[str]:
    if error is None:
        return None
    written = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    return written[-TRACE_MOST:]

async def note(area: str, stage: str, reason: str, *, telegram_id: Optional[int] = None, chat_id: Optional[int] = None,
               detail: Optional[dict[str, Any]] = None, error: Optional[BaseException] = None) -> str:
    noted = Noted(
        code=new_code(), area=area[:32], stage=stage[:48], reason=(reason or "?")[:300],
        telegram_id=telegram_id, chat_id=chat_id, detail=_detail(detail), trace=_trace(error),
    )
    _recent.append(noted)
    logger.warning("incident %s %s/%s: %s user=%s chat=%s %s", noted.code, noted.area, noted.stage, noted.reason,
                   telegram_id, chat_id, noted.detail or "")
    if saving:
        try:
            async with AsyncSessionFactory() as session:
                session.add(Incident(
                    code=noted.code, area=noted.area, stage=noted.stage, reason=noted.reason, telegram_id=telegram_id,
                    chat_id=chat_id, detail=noted.detail, trace=noted.trace, created_at=noted.created_at,
                ))
                await session.execute(delete(Incident).where(Incident.created_at < noted.created_at - KEEP))
                await session.commit()
        except Exception as exc:
            logger.warning("incident %s stayed in memory only: %s", noted.code, exc)
    return noted.code

def _from_row(row: Incident) -> Noted:
    return Noted(code=row.code, area=row.area, stage=row.stage, reason=row.reason, telegram_id=row.telegram_id,
                 chat_id=row.chat_id, detail=row.detail, trace=row.trace, created_at=row.created_at)

def _remembered(area: Optional[str], limit: int) -> list[Noted]:
    found = [one for one in reversed(_recent) if area is None or one.area == area]
    return found[:limit]

async def recent(area: Optional[str] = None, limit: int = 15) -> list[Noted]:
    if not saving:
        return _remembered(area, limit)
    try:
        async with AsyncSessionFactory() as session:
            wanted = select(Incident).order_by(Incident.created_at.desc(), Incident.id.desc()).limit(limit)
            if area is not None:
                wanted = wanted.where(Incident.area == area)
            return [_from_row(row) for row in (await session.execute(wanted)).scalars().all()]
    except Exception as exc:
        logger.warning("incidents are read from memory: %s", exc)
        return _remembered(area, limit)

async def find(code: str) -> Optional[Noted]:
    code = (code or "").strip().upper()
    if not code:
        return None
    kept = next((one for one in reversed(_recent) if one.code == code), None)
    if kept is not None or not saving:
        return kept
    try:
        async with AsyncSessionFactory() as session:
            row = (await session.execute(select(Incident).where(Incident.code == code))).scalar_one_or_none()
            return _from_row(row) if row is not None else None
    except Exception as exc:
        logger.warning("incident %s could not be read: %s", code, exc)
        return None

def forget() -> None:
    _recent.clear()
