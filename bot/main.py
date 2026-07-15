import asyncio
import signal
import sys
from contextlib import suppress
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_API_URL, validate_settings
from utils.logger import get_logger
from utils.osu.api_client import OsuApiClient

from bot.handlers.auth import router as auth_router
from bot.handlers.admin import router as admin_router
from bot.handlers.profile import router as profile_router
from bot.handlers.titles import router as titles_router
from bot.handlers.common import router as common_router
from bot.handlers.start import router as start_router
from bot.handlers.dm_tenant import router as dm_tenant_router
from bot.handlers.leaderboard import router as leaderboard_router
from bot.handlers.maplink import router as maplink_router
from bot.handlers.scorelink import router as scorelink_router
from bot.handlers.pagination import router as pagination_router
from bot.handlers.errors import router as errors_router

from bot.middlewares.api_client_middleware import ApiClientMiddleware
from bot.middlewares.group_restriction_middleware import GroupRestrictionMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.last_seen_middleware import LastSeenMiddleware
from bot.middlewares.startup_filter_middleware import StartupFilterMiddleware
from bot.middlewares.tenant_middleware import TenantMiddleware
from tasks.profile_updater import periodic_profile_updates
from tasks.gpu_watchdog import gpu_watchdog_loop

from db.database import engine, Base, close_engine
from services.image import close_shared_session
from services.oauth.server import OAuthServer, set_bot as oauth_set_bot
from utils.cloud import gpu_power
from db.migrations import run_all_migrations
import db.models  # noqa: F401 — ensure all models registered for create_all

logger = get_logger(__name__)


class App:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.osu_api_client: Optional[OsuApiClient] = None
        self.shutdown_event = asyncio.Event()
        self.profile_updater_task: Optional[asyncio.Task] = None
        self.gpu_watchdog_task: Optional[asyncio.Task] = None
        self.oauth_server: Optional[OAuthServer] = None

    async def setup(self) -> None:
        validate_settings()

        logger.info("Initializing bot and dispatcher...")
        if TELEGRAM_BOT_API_URL:
            session = AiohttpSession(
                api=TelegramAPIServer.from_base(TELEGRAM_BOT_API_URL, is_local=True)
            )
            self.bot = Bot(token=TELEGRAM_BOT_TOKEN, session=session)
            logger.info("Using local Bot API server at %s (2GB uploads)", TELEGRAM_BOT_API_URL)
        else:
            self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher(storage=MemoryStorage())

        self.osu_api_client = OsuApiClient()

        # Middleware order: startup filter → group restriction → rate limit → last seen → api client
        startup_mw = StartupFilterMiddleware()
        self.dp.message.middleware(startup_mw)
        self.dp.callback_query.middleware(startup_mw)

        group_mw = GroupRestrictionMiddleware()
        self.dp.message.middleware(group_mw)
        self.dp.callback_query.middleware(group_mw)

        # Resolve the effective tenant (group→chat.id, DM→user's chosen group)
        # and inject it as `tenant_chat_id` for data-scoped handlers.
        tenant_mw = TenantMiddleware()
        self.dp.message.middleware(tenant_mw)
        self.dp.callback_query.middleware(tenant_mw)

        rate_mw = RateLimitMiddleware()
        self.dp.message.middleware(rate_mw)
        self.dp.callback_query.middleware(rate_mw)

        last_seen_mw = LastSeenMiddleware()
        self.dp.message.middleware(last_seen_mw)
        self.dp.callback_query.middleware(last_seen_mw)

        api_mw = ApiClientMiddleware(self.osu_api_client)
        self.dp.message.middleware(api_mw)
        self.dp.callback_query.middleware(api_mw)

        self.dp.include_router(start_router)
        self.dp.include_router(dm_tenant_router)
        self.dp.include_router(auth_router)
        self.dp.include_router(admin_router)
        self.dp.include_router(profile_router)
        self.dp.include_router(titles_router)
        self.dp.include_router(common_router)
        self.dp.include_router(leaderboard_router)
        # Auto map-card on pasted beatmap links. After command routers so any
        # command carrying a link is handled by its own router first.
        self.dp.include_router(maplink_router)
        # Auto score-card on pasted score links — same rationale/placement as
        # maplink above. No filter overlap: /beatmap*|/b/|/s/ vs /scores/...
        self.dp.include_router(scorelink_router)
        self.dp.include_router(pagination_router)
        # Errors router — must be included LAST so it catches anything that
        # other handlers raise without swallowing.
        self.dp.include_router(errors_router)

        logger.info("Checking/creating database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Running database migrations...")
        await run_all_migrations(engine)

        logger.info("Initializing osu! API client...")
        await self.osu_api_client.initialize()

        logger.info("Starting OAuth server...")
        self.oauth_server = OAuthServer()
        await self.oauth_server.start()
        oauth_set_bot(self.bot)
        gpu_power.set_bot(self.bot)
        await gpu_power.resume_if_already_up()

        logger.info("Starting background profile updater...")
        self.profile_updater_task = asyncio.create_task(
            periodic_profile_updates(self.osu_api_client, self.shutdown_event),
            name="profile_updater"
        )

        logger.info("Starting GPU power watchdog loop...")
        self.gpu_watchdog_task = asyncio.create_task(
            gpu_watchdog_loop(self.shutdown_event),
            name="gpu_watchdog",
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

        if self.gpu_watchdog_task:
            self.gpu_watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.gpu_watchdog_task

        if self.oauth_server:
            await self.oauth_server.stop()


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
