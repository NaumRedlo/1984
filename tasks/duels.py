import asyncio

from services.duels import deadlines, outbox, rating
from utils.logger import get_logger

logger = get_logger("tasks.duels")

EVERY = 1.0

async def run(shutdown_event: asyncio.Event, osu=None, bot=None) -> None:
    from db.database import AsyncSessionFactory
    from services.duels import live

    helpers = [asyncio.create_task(live.hub.run(AsyncSessionFactory, shutdown_event))]
    gateway = _bancho(AsyncSessionFactory, osu, live.hub)
    if gateway is not None:
        helpers.append(asyncio.create_task(gateway.run(shutdown_event)))
    helpers.append(asyncio.create_task(_matching(AsyncSessionFactory, shutdown_event, live.hub.present)))
    helpers.append(asyncio.create_task(_training(AsyncSessionFactory, shutdown_event)))
    helpers.append(asyncio.create_task(_hunting(AsyncSessionFactory, shutdown_event, osu)))
    try:
        await _sweep(AsyncSessionFactory, shutdown_event, _telegram(bot))
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
    coordinator.witness = hub.witnessed
    coordinator.install()
    return gateway

def _telegram(bot):
    if bot is None:
        return None

    async def send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)

    return send

async def _sweep(factory, shutdown_event: asyncio.Event, send=None) -> None:
    from services.duels import notify
    from utils.language import get_language

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=EVERY)
            return
        except asyncio.TimeoutError:
            pass
        try:
            await deadlines.sweep(factory)
            await outbox.sweep(factory)
            await rating.sweep(factory)
            if send is not None:
                await notify.deliver(factory, send, get_language)
        except Exception as exc:
            logger.error("the duel worker stumbled: %s", exc, exc_info=True)

MATCH_EVERY = 5.0

async def _matching(factory, shutdown_event: asyncio.Event, present) -> None:
    from services.duels import matchmaker

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=MATCH_EVERY)
            return
        except asyncio.TimeoutError:
            pass
        try:
            while await matchmaker.offer(factory, present) is not None:
                pass
        except Exception as exc:
            logger.error("duel matching stumbled: %s", exc, exc_info=True)

TRAIN_CHECK_EVERY = 3600.0

async def _training(factory, shutdown_event: asyncio.Event) -> None:
    from services.duels import training

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=TRAIN_CHECK_EVERY)
            return
        except asyncio.TimeoutError:
            pass
        try:
            made = await training.maybe_train(factory)
            if made:
                logger.info("a shadow duel slot model %s waits for review", made)
        except Exception as exc:
            logger.error("duel model training stumbled: %s", exc, exc_info=True)

def hunt_mode(setting: str, turn: int) -> str:
    if setting in ("gaps", "random"):
        return setting
    return "random" if turn % 4 == 3 else "gaps"

async def _hunting(factory, shutdown_event: asyncio.Event, osu) -> None:
    import aiohttp

    from config.settings import DUEL_HUNT_HOURS, DUEL_HUNT_MODE, DUEL_OSU_FOLDER
    from services.duels import balance, importer, mirrors

    if DUEL_HUNT_HOURS <= 0 or osu is None:
        logger.info("Kamerton does not look for maps by itself: DUEL_HUNT_HOURS is 0 or osu! API is missing")
        return
    turn = 0
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=DUEL_HUNT_HOURS * 3600)
            return
        except asyncio.TimeoutError:
            pass
        try:
            model = await balance.working_model(factory)
            if model is None:
                logger.warning("Kamerton hunt skipped: no published model compatible with the analyser")
                continue
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120, sock_read=30)) as session:
                found = await importer.hunt(factory, mirrors.aiohttp_fetch(session), osu, DUEL_OSU_FOLDER, model, hunt_mode(DUEL_HUNT_MODE, turn),
                                            report=lambda said: logger.info("Kamerton hunt: %s", said))
            await balance.note_hunt(factory, found)
            logger.info("Kamerton looked for %s maps: %s", found["slot"], found)
        except Exception as exc:
            logger.error("Kamerton's map hunt stumbled: %s", exc, exc_info=True)
        turn += 1
