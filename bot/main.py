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
from bot.handlers.team import router as team_router
from bot.handlers.maplink import router as maplink_router
from bot.handlers.scorelink import router as scorelink_router
from bot.handlers.pagination import router as pagination_router
from bot.handlers.errors import router as errors_router
from bot.handlers.farm import router as farm_router

from bot.middlewares.api_client_middleware import ApiClientMiddleware
from bot.middlewares.group_restriction_middleware import GroupRestrictionMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.last_seen_middleware import LastSeenMiddleware
from bot.middlewares.startup_filter_middleware import StartupFilterMiddleware
from bot.middlewares.tenant_middleware import TenantMiddleware
from tasks.membership import run as sweep_rosters
from tasks.duels import deliver_notices as deliver_duel_notices
from bot.handlers.membership import router as membership_router

from db.database import engine, close_engine
from db.setup import prepare
from server.side import AppSide
from services.image import close_shared_session
from bot.handlers.auth.linked import notifier as oauth_notifier
from config.settings import BOT_ALONE, RENDER_WORKER_TOKEN, TELEGRAM_THROUGH_ROWS
from utils.memory_report import to_log
from utils.signals import shared as shared_signals
import db.models

logger = get_logger(__name__)

class App:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.osu_api_client: Optional[OsuApiClient] = None
        self.shutdown_event = asyncio.Event()
        self.side: Optional[AppSide] = None
        self.tasks: list[asyncio.Task] = []

    async def setup(self) -> None:
        validate_settings()

        from utils import aiogram_lazy

        if aiogram_lazy.installed():
            logger.info("Telegram types are built on first use: %s", "checked" if aiogram_lazy.verify() else "fell back to all at once")

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

        startup_mw = StartupFilterMiddleware()
        self.dp.message.middleware(startup_mw)
        self.dp.callback_query.middleware(startup_mw)

        group_mw = GroupRestrictionMiddleware()
        self.dp.message.middleware(group_mw)
        self.dp.callback_query.middleware(group_mw)

        tenant_mw = TenantMiddleware()
        self.dp.message.middleware(tenant_mw)
        self.dp.callback_query.middleware(tenant_mw)

        rate_mw = RateLimitMiddleware()
        self.dp.message.middleware(rate_mw)
        self.dp.callback_query.middleware(rate_mw)

        last_seen_mw = LastSeenMiddleware()
        self.dp.message.outer_middleware(last_seen_mw)
        self.dp.callback_query.outer_middleware(last_seen_mw)

        api_mw = ApiClientMiddleware(self.osu_api_client)
        self.dp.message.middleware(api_mw)
        self.dp.callback_query.middleware(api_mw)

        self.dp.include_router(start_router)
        self.dp.include_router(membership_router)
        self.dp.include_router(farm_router)
        self.dp.include_router(dm_tenant_router)
        self.dp.include_router(auth_router)
        self.dp.include_router(admin_router)
        self.dp.include_router(profile_router)
        self.dp.include_router(titles_router)
        self.dp.include_router(common_router)
        self.dp.include_router(leaderboard_router)
        self.dp.include_router(team_router)

        self.dp.include_router(maplink_router)

        self.dp.include_router(scorelink_router)
        self.dp.include_router(pagination_router)

        self.dp.include_router(errors_router)

        logger.info("Checking/creating database tables and running migrations...")
        await prepare(engine)

        from services.render_farm import invites as farm_invites

        await farm_invites.load()
        self.tasks.append(asyncio.create_task(farm_invites.follow(self.shutdown_event), name="render_tokens"))

        from utils.osu import assay_service

        if assay_service.enabled():
            served = await assay_service.health()
            if served:
                logger.info("pp: the assay service answers, on osu! %s", served.get("osu_version"))
            else:
                logger.warning("pp: the assay service at %s does not answer — cards will show no pp until it does",
                               assay_service.settings.ASSAY_URL)
        else:
            logger.warning("pp: ASSAY_URL is unset — cards will show no pp of their own")

        if RENDER_WORKER_TOKEN:
            import hashlib

            short = hashlib.sha256(RENDER_WORKER_TOKEN.encode()).hexdigest()[:8]
            logger.info(
                "Render worker token: %d chars, %s", len(RENDER_WORKER_TOKEN), short
            )

        logger.info("Initializing osu! API client...")
        await self.osu_api_client.initialize()

        from db.database import AsyncSessionFactory
        from services.render_farm import orders as farm_orders
        from services.render_farm import pairing as farm_pairing
        from services.render_farm.telegram_direct import Direct
        from services.render_farm.telegram_rows import Carrier, Rows

        direct = Direct(self.bot)
        told_linked = oauth_notifier(self.bot)
        through_rows = BOT_ALONE or TELEGRAM_THROUGH_ROWS
        if through_rows:
            logger.info("Telegram is asked through rows of the database")
            carrier = Carrier(AsyncSessionFactory, direct, shared_signals, linked=told_linked)
            self.tasks.append(asyncio.create_task(carrier.run(self.shutdown_event), name="telegram_tasks"))

        if BOT_ALONE:
            logger.info("The bot runs alone: the app's side is another process")
            self.tasks.append(asyncio.create_task(deliver_duel_notices(self.shutdown_event, self.bot), name="duel_notices"))
        else:
            self.side = AppSide(self.osu_api_client, self.shutdown_event)
            if through_rows:
                asking = Rows(AsyncSessionFactory, shared_signals)
                await self.side.start(asking, asking.linked, duel_bot=self.bot)
            else:
                await self.side.start(direct, told_linked, duel_bot=self.bot)

        try:
            await farm_orders.resume(self.bot)
        except Exception as exc:
            logger.warning("render orders left by the last run were not picked up: %s", exc)

        try:
            me = await self.bot.get_me()
            await farm_pairing.publish_bot_username(me.username or "")
        except Exception as exc:
            logger.warning("cannot learn my own username, pairing links stay bare: %s", exc)

        logger.info("Starting the roster sweep...")
        self.tasks.append(asyncio.create_task(sweep_rosters(self.bot, self.shutdown_event), name="membership"))

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

        if self.side is not None:
            await self.side.stop()
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks = []
        await shared_signals.stop()

        if self.osu_api_client:
            await self.osu_api_client.close()

        if self.bot:
            await self.bot.session.close()

        await close_shared_session()
        from utils.osu import assay_service

        await assay_service.close()
        await close_engine()

        logger.info("Shutdown completed.")

async def main() -> None:
    app = App()
    await app.setup()

    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, app.shutdown_event.set)
    if hasattr(signal, "SIGUSR1"):
        loop.add_signal_handler(signal.SIGUSR1, to_log, logger)

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
