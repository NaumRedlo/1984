import asyncio
import logging
import signal
import sys
from contextlib import suppress
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import TELEGRAM_BOT_TOKEN, validate_settings
from utils.logger import get_logger
from utils.osu_api_client import OsuApiClient

from bot.handlers.start_handlers import router as start_router
from bot.handlers.auth_handlers import router as auth_router
from bot.handlers.profile_handlers import router as profile_router
from bot.handlers.hps_handlers import router as hps_router
from bot.handlers.help_handlers import router as help_router
from bot.handlers.recent_handlers import router as recent_router
from bot.handlers.compare_handlers import router as compare_router
from bot.handlers.leaderboard_handlers import router as leaderboard_router
from bot.handlers.admin_handlers import router as admin_router
from bot.handlers.bounty_handlers import router as bounty_router

from bot.middlewares.api_client_middleware import ApiClientMiddleware
from bot.middlewares.group_restriction_middleware import GroupRestrictionMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from tasks.profile_updater import periodic_profile_updates

from db.database import engine, Base, close_engine
from services.image_generator import close_shared_session
from db.migrations.add_leaderboard_fields import run_migration
from db.migrations.add_avatar_cover_fields import run_avatar_migration
from db.migrations.add_beatmapset_id import run_beatmapset_id_migration
from db.migrations.add_total_score import run_total_score_migration
from db.migrations.add_avatar_cover_cache import run_avatar_cache_migration
import db.models  # noqa: F401 — ensure all models registered for create_all

logger = get_logger(__name__)


class App:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.osu_api_client: Optional[OsuApiClient] = None
        self.shutdown_event = asyncio.Event()
        self.profile_updater_task: Optional[asyncio.Task] = None

    async def setup(self) -> None:
        validate_settings()

        logger.info("Initializing bot and dispatcher...")
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher(storage=MemoryStorage())

        self.osu_api_client = OsuApiClient()

        # Middleware order: group restriction → rate limit → api client
        group_mw = GroupRestrictionMiddleware()
        self.dp.message.middleware(group_mw)
        self.dp.callback_query.middleware(group_mw)

        rate_mw = RateLimitMiddleware()
        self.dp.message.middleware(rate_mw)
        self.dp.callback_query.middleware(rate_mw)

        api_mw = ApiClientMiddleware(self.osu_api_client)
        self.dp.message.middleware(api_mw)
        self.dp.callback_query.middleware(api_mw)

        self.dp.include_router(start_router)
        self.dp.include_router(auth_router)
        self.dp.include_router(admin_router)
        self.dp.include_router(profile_router)
        self.dp.include_router(hps_router)
        self.dp.include_router(bounty_router)
        self.dp.include_router(help_router)
        self.dp.include_router(recent_router)
        self.dp.include_router(compare_router)
        self.dp.include_router(leaderboard_router)

        logger.info("Checking/creating database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Running database migrations...")
        await run_migration(engine)
        await run_avatar_migration(engine)
        await run_beatmapset_id_migration(engine)
        await run_total_score_migration(engine)
        await run_avatar_cache_migration(engine)

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

        await close_shared_session()
        await close_engine()

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
