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
from utils.osu.api_client import OsuApiClient

from bot.handlers.auth import router as auth_router
from bot.handlers.admin import router as admin_router
from bot.handlers.profile import router as profile_router
from bot.handlers.common import router as common_router
from bot.handlers.start import router as start_router
from bot.handlers.hps import router as hps_router
from bot.handlers.bounty import router as bounty_router
from bot.handlers.leaderboard import router as leaderboard_router
from bot.handlers.duel import router as duel_router
from bot.handlers.maplink import router as maplink_router
from bot.handlers.pagination import router as pagination_router
from bot.handlers.errors import router as errors_router

from bot.middlewares.api_client_middleware import ApiClientMiddleware
from bot.middlewares.group_restriction_middleware import GroupRestrictionMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.last_seen_middleware import LastSeenMiddleware
from bot.middlewares.startup_filter_middleware import StartupFilterMiddleware
from tasks.profile_updater import periodic_profile_updates
from tasks.bounty_expirer import bounty_expirer_loop
from tasks.bounty_weekly import weekly_digest_loop, expiry_reminder_loop
from tasks.bounty_weekly_generator import weekly_generator_loop
from tasks.bounty_auto_checker import bounty_auto_checker_loop

from db.database import engine, Base, close_engine
from services.image import close_shared_session
from services.oauth.server import OAuthServer, set_bot as oauth_set_bot
from services.duel.duel_manager import init_duel_manager, recover_active_duels
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
        self.bounty_expirer_task: Optional[asyncio.Task] = None
        self.weekly_digest_task: Optional[asyncio.Task] = None
        self.expiry_reminder_task: Optional[asyncio.Task] = None
        self.weekly_generator_task: Optional[asyncio.Task] = None
        self.bounty_checker_task: Optional[asyncio.Task] = None
        self.oauth_server: Optional[OAuthServer] = None

    async def setup(self) -> None:
        validate_settings()

        logger.info("Initializing bot and dispatcher...")
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
        self.dp.include_router(auth_router)
        self.dp.include_router(admin_router)
        self.dp.include_router(profile_router)
        self.dp.include_router(common_router)
        self.dp.include_router(hps_router)
        self.dp.include_router(bounty_router)
        self.dp.include_router(leaderboard_router)
        self.dp.include_router(duel_router)
        # Auto map-card on pasted beatmap links. After command routers so any
        # command carrying a link is handled by its own router first.
        self.dp.include_router(maplink_router)
        self.dp.include_router(pagination_router)
        # Errors router — must be included LAST so it catches anything that
        # other handlers raise without swallowing.
        self.dp.include_router(errors_router)

        logger.info("Checking/creating database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Running database migrations...")
        await run_all_migrations(engine)

        # One-shot DUEL pool health check — surfaces pool size and maps
        # missing a length (which drop out of selection). Diagnostic only;
        # never blocks startup.
        try:
            from services.duel.map_selector import log_pool_health
            await log_pool_health()
        except Exception as e:
            logger.warning(f"pool_health: startup check failed: {e}")

        logger.info("Initializing osu! API client...")
        await self.osu_api_client.initialize()

        logger.info("Starting OAuth server...")
        self.oauth_server = OAuthServer()
        await self.oauth_server.start()
        oauth_set_bot(self.bot)
        init_duel_manager(self.bot, self.osu_api_client)

        logger.info("Recovering active DUEL duels...")
        asyncio.create_task(
            recover_active_duels(self.bot, self.osu_api_client),
            name="duel_recovery",
        )

        # Connect to Bancho IRC if credentials are configured
        from services.bancho_irc import get_irc_client
        from services.duel.irc_room import rejoin_active_duel_channels
        irc = get_irc_client()
        # Register reconnect hook BEFORE connecting so the initial connect
        # also re-joins channels of any duel left in flight by a previous run.
        irc.add_on_reconnect(rejoin_active_duel_channels)
        if irc.username and irc.password:
            logger.info("Connecting to Bancho IRC...")
            connected = await irc.connect()
            if connected:
                logger.info("Bancho IRC connected")
            else:
                logger.warning("Bancho IRC connection failed, duels will use fallback mode")
        else:
            logger.info("Bancho IRC credentials not configured, using fallback mode")

        logger.info("Starting background profile updater...")
        self.profile_updater_task = asyncio.create_task(
            periodic_profile_updates(self.osu_api_client, self.shutdown_event),
            name="profile_updater"
        )

        logger.info("Starting bounty expirer loop...")
        self.bounty_expirer_task = asyncio.create_task(
            bounty_expirer_loop(self.shutdown_event),
            name="bounty_expirer",
        )

        logger.info("Starting bounty weekly digest loop...")
        self.weekly_digest_task = asyncio.create_task(
            weekly_digest_loop(self.bot, self.shutdown_event),
            name="bounty_weekly_digest",
        )

        logger.info("Starting bounty weekly pool generator...")
        self.weekly_generator_task = asyncio.create_task(
            weekly_generator_loop(
                self.bot, self.shutdown_event,
                osu_api_client=self.osu_api_client,
            ),
            name="bounty_weekly_generator",
        )

        logger.info("Starting bounty expiry reminder loop...")
        self.expiry_reminder_task = asyncio.create_task(
            expiry_reminder_loop(self.bot, self.shutdown_event),
            name="bounty_expiry_reminder",
        )

        logger.info("Starting bounty auto-checker loop...")
        self.bounty_checker_task = asyncio.create_task(
            bounty_auto_checker_loop(self.bot, self.osu_api_client, self.shutdown_event),
            name="bounty_auto_checker",
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

        if self.bounty_expirer_task:
            self.bounty_expirer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.bounty_expirer_task

        if self.weekly_digest_task:
            self.weekly_digest_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.weekly_digest_task

        if self.expiry_reminder_task:
            self.expiry_reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.expiry_reminder_task

        if self.weekly_generator_task:
            self.weekly_generator_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.weekly_generator_task

        if self.bounty_checker_task:
            self.bounty_checker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.bounty_checker_task

        if self.oauth_server:
            await self.oauth_server.stop()

        # Disconnect Bancho IRC
        from services.bancho_irc import get_irc_client
        irc = get_irc_client()
        if irc.connected:
            await irc.disconnect()

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
