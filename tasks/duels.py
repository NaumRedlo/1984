import asyncio

from services.duels import deadlines, outbox
from utils.logger import get_logger

logger = get_logger("tasks.duels")

EVERY = 1.0

async def run(shutdown_event: asyncio.Event) -> None:
    from db.database import AsyncSessionFactory

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=EVERY)
            return
        except asyncio.TimeoutError:
            pass
        try:
            await deadlines.sweep(AsyncSessionFactory)
            await outbox.sweep(AsyncSessionFactory)
        except Exception as exc:
            logger.error("the duel worker stumbled: %s", exc, exc_info=True)
