from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import or_, select

from db.models.duel import DuelCommand
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("duels.outbox")

CLAIM_SECONDS = 60
MOST_ATTEMPTS = 10
LONGEST_PAUSE = 300

Handler = Callable[[dict[str, Any]], Awaitable[None]]

HANDLERS: dict[str, Handler] = {}

def _pause(attempts: int) -> timedelta:
    return timedelta(seconds=min(2 ** attempts, LONGEST_PAUSE))

async def claim(factory, now: Optional[datetime] = None, limit: int = 20) -> list[dict[str, Any]]:
    now = now or utcnow()
    if not HANDLERS:
        return []
    async with factory() as session:
        async with session.begin():
            rows = (await session.scalars(
                select(DuelCommand)
                .where(or_(DuelCommand.status == "pending", DuelCommand.status == "running"),
                       DuelCommand.next_attempt_at <= now, DuelCommand.kind.in_(list(HANDLERS)))
                .order_by(DuelCommand.id).limit(limit).with_for_update(skip_locked=True)
            )).all()
            taken = []
            for row in rows:
                row.status = "running"
                row.attempts += 1
                row.next_attempt_at = now + timedelta(seconds=CLAIM_SECONDS)
                taken.append({"id": row.id, "duel": row.duel_id, "kind": row.kind, "payload": row.payload,
                              "attempts": row.attempts, "epoch": row.room_epoch})
            return taken

async def settle(factory, command_id: int, attempts: int, error: Optional[str], now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            row = await session.scalar(select(DuelCommand).where(DuelCommand.id == command_id).with_for_update())
            if row is None or row.status != "running" or row.attempts != attempts:
                return "lost"
            if error is None:
                row.status = "done"
                row.done_at = now
                row.last_error = None
            elif row.attempts >= MOST_ATTEMPTS:
                row.status = "failed"
                row.last_error = error
            else:
                row.status = "pending"
                row.last_error = error
                row.next_attempt_at = now + _pause(row.attempts)
            return row.status

async def sweep(factory, now: Optional[datetime] = None) -> int:
    finished = 0
    for command in await claim(factory, now):
        error = None
        try:
            await HANDLERS[command["kind"]](command)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("duel command %s %s failed: %s", command["id"], command["kind"], error)
        if await settle(factory, command["id"], command["attempts"], error, now) == "done":
            finished += 1
    return finished
