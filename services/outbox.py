from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import or_, select

from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("outbox")

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


def _plain(row) -> dict[str, Any]:
    return {"id": row.id, "kind": row.kind, "payload": row.payload, "attempts": row.attempts}


class Outbox:
    def __init__(self, model, handlers: Callable[[], dict[str, Handler]], name: str, told=_plain, due=None, once=None,
                 answered=None, settled=None, claim_seconds: int = 60, most_attempts: int = 10, longest_pause: int = 300):
        self.model = model
        self._handlers = handlers
        self.name = name
        self.told = told
        self.due = due
        self.once = once
        self.answered = answered
        self.settled = settled
        self.claim_seconds = claim_seconds
        self.most_attempts = most_attempts
        self.longest_pause = longest_pause

    @property
    def handlers(self) -> dict[str, Handler]:
        return self._handlers()

    def _pause(self, attempts: int) -> timedelta:
        return timedelta(seconds=min(2 ** attempts, self.longest_pause))

    async def claim(self, factory, now: Optional[datetime] = None, limit: int = 20) -> list[dict[str, Any]]:
        now = now or utcnow()
        handlers = self.handlers
        if not handlers or limit <= 0:
            return []
        model = self.model
        wanted = [or_(model.status == "pending", model.status == "running"),
                  model.next_attempt_at <= now, model.kind.in_(list(handlers))]
        if self.due is not None:
            wanted.append(self.due(now))
        async with factory() as session:
            async with session.begin():
                rows = (await session.scalars(
                    select(model).where(*wanted).order_by(model.id).limit(limit).with_for_update(skip_locked=True)
                )).all()
                taken = []
                for row in rows:
                    row.status = "running"
                    row.attempts += 1
                    row.next_attempt_at = now + timedelta(seconds=self.claim_seconds)
                    taken.append(self.told(row))
                return taken

    async def settle(self, factory, row_id: int, attempts: int, error: Optional[str], now: Optional[datetime] = None,
                     answer: Any = None) -> str:
        now = now or utcnow()
        model = self.model
        async with factory() as session:
            async with session.begin():
                row = await session.scalar(select(model).where(model.id == row_id).with_for_update())
                if row is None or row.status != "running" or row.attempts != attempts:
                    return "lost"
                if error is None:
                    row.status = "done"
                    row.done_at = now
                    row.last_error = None
                    if self.answered is not None:
                        self.answered(row, answer)
                elif row.attempts >= self.most_attempts or (self.once is not None and self.once(row)):
                    row.status = "failed"
                    row.last_error = error
                else:
                    row.status = "pending"
                    row.last_error = error
                    row.next_attempt_at = now + self._pause(row.attempts)
                if self.settled is not None:
                    await self.settled(session, row)
                return row.status

    async def carry(self, factory, taken: dict[str, Any], now: Optional[datetime] = None) -> str:
        error, answer = None, None
        try:
            answer = await self.handlers[taken["kind"]](taken)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("%s %s %s failed: %s", self.name, taken["id"], taken["kind"], error)
        return await self.settle(factory, taken["id"], taken["attempts"], error, now, answer)

    async def sweep(self, factory, now: Optional[datetime] = None) -> int:
        finished = 0
        for taken in await self.claim(factory, now):
            if await self.carry(factory, taken, now) == "done":
                finished += 1
        return finished
