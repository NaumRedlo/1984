import asyncio

from services.render_farm import map_checks
from utils.logger import get_logger

logger = get_logger("tasks.map_checks")

EVERY = 60.0
FIRST_AFTER = 90.0

async def run(osu, shutdown_event: asyncio.Event) -> None:
    from db.database import AsyncSessionFactory

    wait = FIRST_AFTER
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=wait)
            return
        except asyncio.TimeoutError:
            pass
        wait = EVERY
        try:
            async with AsyncSessionFactory() as session:
                await map_checks.check(session, osu)
        except Exception as exc:
            logger.error("checking the maps of local scores stumbled: %s", exc, exc_info=True)
