import asyncio
import os

from services import membership
from utils.logger import get_logger

logger = get_logger("tasks.membership")

EVERY_HOURS = float(os.getenv("MEMBERSHIP_EVERY_HOURS", "6"))
FIRST_AFTER = 120.0

async def run(bot, shutdown_event: asyncio.Event) -> None:
    if EVERY_HOURS <= 0:
        logger.info("chat rosters are not swept: MEMBERSHIP_EVERY_HOURS is 0")
        return
    from db.database import AsyncSessionFactory

    wait = FIRST_AFTER
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=wait)
            return
        except asyncio.TimeoutError:
            pass
        wait = EVERY_HOURS * 3600
        try:
            said = await membership.sweep(bot, AsyncSessionFactory)
            came = sum(len(told.added) for told in said)
            left = sum(len(told.removed) for told in said)
            skipped = [told.chat_id for told in said if told.skipped]
            logger.info("chat rosters swept: %d chats, %d came, %d left, skipped %s", len(said), came, left, skipped)
        except Exception as exc:
            logger.error("the roster sweep stumbled: %s", exc, exc_info=True)
