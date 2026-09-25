import asyncio
import time
from typing import Callable, Optional

from sqlalchemy import select

from db.models.user import User
from utils.logger import get_logger

logger = get_logger("tasks.live_tracker")

TICK_SECONDS = 20.0
EACH_SECONDS = 180.0
ROUND_MOST = 10
RECENT_LIMIT = 20
NUDGE_AFTER = (5.0, 30.0)
CONCURRENT = 3


class LiveTracker:
    def __init__(self, api_client, *, clock: Callable[[], float] = time.monotonic):
        self.api_client = api_client
        self.clock = clock
        self.asked: dict[int, float] = {}
        self.nudged: dict[int, list[float]] = {}

    def nudge(self, osu_user_id: int) -> None:
        now = self.clock()
        self.nudged[osu_user_id] = [now + after for after in NUDGE_AFTER]

    def due(self, known: list[int]) -> list[int]:
        now = self.clock()
        picked: list[int] = []
        for osu_user_id, times in list(self.nudged.items()):
            if times and times[0] <= now:
                picked.append(osu_user_id)
                self.nudged[osu_user_id] = [at for at in times if at > now]
            if not self.nudged.get(osu_user_id):
                self.nudged.pop(osu_user_id, None)
        waiting = sorted(
            (osu_user_id for osu_user_id in set(known) if osu_user_id not in picked and now - self.asked.get(osu_user_id, float("-inf")) >= EACH_SECONDS),
            key=lambda osu_user_id: self.asked.get(osu_user_id, float("-inf")),
        )
        room = max(0, ROUND_MOST - len(picked))
        return picked + waiting[:room]

    async def known(self) -> list[int]:
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            found = await session.execute(select(User.osu_user_id).where(User.osu_user_id.isnot(None), User.chat_id < 0).distinct())
            return [row[0] for row in found.all()]

    async def catch(self, osu_user_id: int) -> int:
        from bot.handlers.profile.recent import _play_from_score
        from db.database import AsyncSessionFactory
        from utils.title_progress import evaluate_recent_plays

        self.asked[osu_user_id] = self.clock()
        scores = await self.api_client.get_user_recent_scores(osu_user_id, limit=RECENT_LIMIT, mode="osu")
        if not scores:
            return 0
        synced = 0
        async with AsyncSessionFactory() as session:
            users = (await session.execute(select(User).where(User.osu_user_id == osu_user_id))).scalars().all()
            plays = [_play_from_score(score) for score in scores]
            for user in users:
                synced += await self.api_client.sync_user_map_attempts(user, session, scores)
                await evaluate_recent_plays(user, plays, session)
            await session.commit()
        return synced

    async def round(self) -> int:
        chosen = self.due(await self.known())
        if not chosen:
            return 0
        gate = asyncio.Semaphore(CONCURRENT)

        async def one(osu_user_id: int) -> int:
            async with gate:
                try:
                    return await self.catch(osu_user_id)
                except Exception as exc:
                    logger.warning("recent plays of %s could not be caught: %s", osu_user_id, exc)
                    return 0

        caught = sum(await asyncio.gather(*(one(osu_user_id) for osu_user_id in chosen)))
        if caught:
            logger.info("caught %d plays from %d players", caught, len(chosen))
        return caught

    async def run(self, shutdown_event: asyncio.Event) -> None:
        while not shutdown_event.is_set():
            try:
                await self.round()
            except Exception as exc:
                logger.error("the live tracker stumbled: %s", exc, exc_info=True)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                continue


_current: Optional[LiveTracker] = None


def set_current(tracker: Optional[LiveTracker]) -> None:
    global _current
    _current = tracker


def nudge(osu_user_id: int) -> bool:
    if _current is None:
        return False
    _current.nudge(osu_user_id)
    return True
