import asyncio
import json
import time
from dataclasses import dataclass
from time import monotonic
from typing import Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.roster")

GONE_AFTER = 60.0
TOLD_KEY = "render_workers_taking"
TELL_EVERY = 15.0
TOLD_FOR = 60.0

@dataclass
class Worker:
    key: str
    name: str
    owner: int
    first_seen: float
    last_seen: float
    build: Optional[str] = None
    take: bool = True
    delivered: int = 0
    handed_back: int = 0

class Roster:
    def __init__(self) -> None:
        self._seen: dict[str, Worker] = {}
        self.telling = False

    def hello(self, key: str, name: str, owner: int = 0, *, build: Optional[str] = None,
              take: Optional[bool] = None, now: Optional[float] = None) -> Worker:
        now = now if now is not None else monotonic()
        worker = self._seen.get(key)
        if worker is None:
            worker = Worker(key=key, name=name, owner=owner, first_seen=now, last_seen=now)
            self._seen[key] = worker
            logger.info("worker %s joined the farm", name or key[:8])
        worker.last_seen = now
        if name:
            worker.name = name
        if build:
            worker.build = build
        if take is not None:
            worker.take = take
        return worker

    def delivered(self, key: str) -> None:
        worker = self._seen.get(key)
        if worker:
            worker.delivered += 1

    def handed_back(self, key: str) -> None:
        worker = self._seen.get(key)
        if worker:
            worker.handed_back += 1

    def here(self, *, now: Optional[float] = None) -> list[Worker]:
        now = now if now is not None else monotonic()
        for key, worker in list(self._seen.items()):
            if now - worker.last_seen > GONE_AFTER:
                logger.info("worker %s left the farm", worker.name or key[:8])
                del self._seen[key]
        return sorted(self._seen.values(), key=lambda w: w.first_seen)

    def taking(self, *, now: Optional[float] = None) -> int:
        return sum(1 for w in self.here(now=now) if w.take)

    def _factory(self):
        from db.database import AsyncSessionFactory

        return AsyncSessionFactory()

    async def _write(self, taking: int) -> None:
        from db.models.bot_settings import BotSettings

        said = json.dumps({"taking": taking, "at": time.time()})
        async with self._factory() as session:
            kept = await session.get(BotSettings, TOLD_KEY)
            if kept is None:
                session.add(BotSettings(key=TOLD_KEY, value=said))
            else:
                kept.value = said
            await session.commit()

    async def tell(self, shutdown: asyncio.Event) -> None:
        self.telling = True
        try:
            while not shutdown.is_set():
                try:
                    await self._write(self.taking())
                except Exception as exc:
                    logger.warning("could not write how many workers are taking: %s", exc)
                try:
                    await asyncio.wait_for(shutdown.wait(), TELL_EVERY)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.telling = False

    async def taking_anywhere(self) -> int:
        if self.telling or self._seen:
            return self.taking()
        from db.models.bot_settings import BotSettings

        try:
            async with self._factory() as session:
                kept = await session.get(BotSettings, TOLD_KEY)
            said = json.loads(kept.value) if kept is not None and kept.value else {}
        except Exception as exc:
            logger.warning("could not read how many workers are taking: %s", exc)
            return 0
        if time.time() - float(said.get("at") or 0.0) > TOLD_FOR:
            return 0
        return int(said.get("taking") or 0)

roster = Roster()
