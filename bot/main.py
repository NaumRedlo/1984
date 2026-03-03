import asyncio
import logging
import signal
import sys
from contextlib import suppress
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import TELEGRAM_BOT_TOKEN
from utils.logger import get_logger
from utils.osu_api_client import OsuApiClient

from bot.handlers.start_handlers import router as start_router
from bot.handlers.auth_handlers import router as auth_router
from bot.handlers.profile_handlers import router as profile_router
from bot.handlers.hps_handlers import router as hps_router
from bot.handlers.help_handlers import router as help_router
from bot.handlers.recent_handlers import router as recent_router

from bot.middlewares.api_client_middleware import ApiClientMiddleware
from tasks.profile_updater import periodic_profile_updates

from db.database import engine, Base

logger = get_logger(__name__)


class App:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.osu_api_client: Optional[OsuApiClient] = None
        self.shutdown_event = asyncio.Event()
        self.profile_updater_task: Optional[asyncio.Task] = None

    async def setup(self) -> None:
        logger.info("Initializing bot and dispatcher...")
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher(storage=MemoryStorage())

        self.osu_api_client = OsuApiClient()
        self.dp.message.middleware(ApiClientMiddleware(self.osu_api_client))

        self.dp.include_router(start_router)
        self.dp.include_router(auth_router)
        self.dp.include_router(profile_router)
        self.dp.include_router(hps_router)
        self.dp.include_router(help_router)
        self.dp.include_router(recent_router)

        logger.info("Checking/creating database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Initializing osu! API client...")
        await self.osu_api_client.initialize()

        logger.info("Starting background profile updater...")
        self.profile_updater_task = asyncio.create_task(
            periodic_profile_updates(self.osu_api_client, self.shutdown_event), 
            name="profile_updater"
        )

    async def start(self) -> None:
        assert self.bot is not None
        assert self.dp is not None

        logger.info("Starting polling...")
        await self.dp.start_polling(
            self.bot,
            drop_pending_updates=True,
        )

    async def shutdown(self) -> None:
        logger.info("Shutting down application...")

        self.shutdown_event.set()

        if self.profile_updater_task:
            self.profile_updater_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.profile_updater_task

        if self.osu_api_client:
            await self.osu_api_client.close()

        if self.bot:
            await self.bot.session.close()

        logger.info("Shutdown completed.")


async def main() -> None:
    app = App()
    await app.setup()

    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, app.shutdown_event.set)

    try:
        await app.start()
    except asyncio.CancelledError:
        logger.info("Polling cancelled.")
    except Exception:
        logger.critical("Critical error during polling.", exc_info=True)
        raise
    finally:
        await app.shutdown()


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
