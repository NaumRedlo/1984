import asyncio

from services.duels import deadlines, outbox
from utils.logger import get_logger

logger = get_logger("tasks.duels")

EVERY = 1.0

async def run(shutdown_event: asyncio.Event, osu=None) -> None:
    from db.database import AsyncSessionFactory
    from services.duels import live

    helpers = [asyncio.create_task(live.hub.run(AsyncSessionFactory, shutdown_event))]
    gateway = _bancho(AsyncSessionFactory, osu, live.hub)
    if gateway is not None:
        helpers.append(asyncio.create_task(gateway.run(shutdown_event)))
    try:
        await _sweep(AsyncSessionFactory, shutdown_event)
    finally:
        for helper in helpers:
            helper.cancel()

def _bancho(factory, osu, hub):
    from config.settings import BANCHO_HOST, BANCHO_IRC_PASSWORD, BANCHO_PORT, BANCHO_USERNAME, DUEL_CREATED_ROOM_LIMIT
    from services.duels.bancho import Gateway, TcpTransport
    from services.duels.coordinator import Coordinator

    if not BANCHO_USERNAME or not BANCHO_IRC_PASSWORD or osu is None:
        logger.info("duels run without Bancho: BANCHO_USERNAME or BANCHO_IRC_PASSWORD is not set")
        return None
    gateway = Gateway(BANCHO_USERNAME, BANCHO_IRC_PASSWORD, lambda: TcpTransport(BANCHO_HOST, BANCHO_PORT))
    coordinator = Coordinator(factory, gateway, osu, room_limit=DUEL_CREATED_ROOM_LIMIT)
    coordinator.present = hub.present
    coordinator.install()
    return gateway

async def _sweep(factory, shutdown_event: asyncio.Event) -> None:
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=EVERY)
            return
        except asyncio.TimeoutError:
            pass
        try:
            await deadlines.sweep(factory)
            await outbox.sweep(factory)
        except Exception as exc:
            logger.error("the duel worker stumbled: %s", exc, exc_info=True)
