import asyncio
import time
from typing import Callable, Optional

from sqlalchemy import or_, select

from db.models.user import User
from services import recent_plays
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
        allowed = set(known)
        for osu_user_id, times in list(self.nudged.items()):
            if osu_user_id not in allowed:
                self.nudged.pop(osu_user_id, None)
                continue
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

        from db.models.player import Player
        from services.render_farm import invites

        async with AsyncSessionFactory() as session:
            found = await session.execute(select(User.osu_user_id).where(User.osu_user_id.isnot(None), User.chat_id < 0).distinct())
            known = [row[0] for row in found.all()]
            signed = invites.linked_players()
            if signed:
                alone = await session.execute(select(Player.osu_user_id).where(Player.id.in_(signed)))
                known += [row[0] for row in alone.all() if row[0] not in known]
        active = await self.active()
        for osu_user_id in active:
            self.asked.pop(osu_user_id, None)
        return [osu_user_id for osu_user_id in known if osu_user_id not in active]

    async def active(self, session=None) -> set[int]:
        from db.database import AsyncSessionFactory
        from db.models.player import Player
        from services.render_farm import invites

        owners = invites.present()
        players = {owner.player_id for owner in owners if owner.player_id is not None}
        telegram = {owner.telegram_id for owner in owners if owner.telegram_id}
        if not players and not telegram:
            return set()
        async def read(session):
            found = await session.execute(select(Player.osu_user_id).where(or_(Player.id.in_(players), Player.telegram_id.in_(telegram))))
            active = {row[0] for row in found.all() if row[0] is not None}
            if telegram:
                legacy = await session.execute(select(User.osu_user_id).where(User.telegram_id.in_(telegram)))
                active.update(row[0] for row in legacy.all() if row[0] is not None)
            return active
        if session is not None:
            return await read(session)
        async with AsyncSessionFactory() as current:
            return await read(current)

    async def absent(self, osu_user_id: int, session=None) -> bool:
        if osu_user_id not in await self.active(session):
            return True
        self.asked.pop(osu_user_id, None)
        self.nudged.pop(osu_user_id, None)
        return False

    async def catch(self, osu_user_id: int) -> int:
        from bot.handlers.profile.recent import _play_from_score
        from db.database import AsyncSessionFactory
        from utils.title_progress import evaluate_recent_plays

        if not await self.absent(osu_user_id):
            return 0
        self.asked[osu_user_id] = self.clock()
        scores = await self.api_client.get_user_recent_scores(osu_user_id, limit=RECENT_LIMIT, mode="osu")
        if not scores or not await self.absent(osu_user_id):
            return 0
        synced = 0
        async with recent_plays.serial(osu_user_id), AsyncSessionFactory() as session:
            if not await self.absent(osu_user_id, session):
                return 0
            users = (await session.execute(select(User).where(User.osu_user_id == osu_user_id))).scalars().all()
            if not users:
                from db.models.player import Player

                users = (await session.execute(select(Player).where(Player.osu_user_id == osu_user_id))).scalars().all()
            done: set[int] = set()
            for user in users:
                if user.player_id is not None and user.player_id in done:
                    continue
                done.add(user.player_id)
                fresh = await recent_plays.unseen(session, user.player_id, scores, stored=recent_plays.is_linked(user))
                if not fresh:
                    continue
                plays = [_play_from_score(score) for score in fresh]
                synced += await self.api_client.sync_user_map_attempts(user, session, fresh)
                await evaluate_recent_plays(user, plays, session)
            if not await self.absent(osu_user_id, session):
                await session.rollback()
                return 0
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
