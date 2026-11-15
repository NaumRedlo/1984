import asyncio
import os
import signal
import sys

os.environ.setdefault("LOG_NAME", "server")

from config.settings import RENDER_WORKER_TOKEN, validate_settings
from db.database import AsyncSessionFactory, close_engine, engine
from db.setup import prepare
from server.side import AppSide
from services.image import close_shared_session
from services.render_farm import invites
from services.render_farm.telegram_rows import Rows
from utils.logger import get_logger
from utils.memory_report import to_log
from utils.osu import assay_service
from utils.osu.api_client import OsuApiClient
from utils.signals import shared as shared_signals
import db.models

logger = get_logger(__name__)


async def main() -> None:
    validate_settings(telegram=False)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)
    if hasattr(signal, "SIGUSR1"):
        loop.add_signal_handler(signal.SIGUSR1, to_log, logger)

    logger.info("Checking/creating database tables and running migrations...")
    await prepare(engine)
    await invites.load()

    if assay_service.enabled():
        served = await assay_service.health()
        if served:
            logger.info("pp: the assay service answers, on osu! %s", served.get("osu_version"))
        else:
            logger.warning("pp: the assay service at %s does not answer", assay_service.settings.ASSAY_URL)
    if not RENDER_WORKER_TOKEN:
        logger.info("no shared render worker token is set")

    osu = OsuApiClient()
    await osu.initialize()
    telegram = Rows(AsyncSessionFactory, shared_signals)
    side = AppSide(osu, shutdown)
    try:
        await side.start(telegram, telegram.linked)
        logger.info("The server is up without the bot in its process.")
        await shutdown.wait()
    finally:
        logger.info("Shutting down the server...")
        shutdown.set()
        await side.stop()
        await shared_signals.stop()
        await osu.close()
        await close_shared_session()
        await assay_service.close()
        await close_engine()
        logger.info("Shutdown completed.")


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Stopped by user (Ctrl+C).")
    except Exception:
        logger.critical("Unhandled top-level exception.", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run()
