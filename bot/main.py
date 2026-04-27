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
from bot.handlers.bsk import router as bsk_router

from bot.middlewares.api_client_middleware import ApiClientMiddleware
from bot.middlewares.group_restriction_middleware import GroupRestrictionMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.last_seen_middleware import LastSeenMiddleware
from tasks.profile_updater import periodic_profile_updates

from db.database import engine, Base, close_engine
from services.image import close_shared_session
from services.oauth.server import OAuthServer, set_bot as oauth_set_bot
from services.bsk.duel_manager import init_duel_manager
from db.migrations.add_leaderboard_fields import run_migration
from db.migrations.add_avatar_cover_fields import run_avatar_migration
from db.migrations.add_beatmapset_id import run_beatmapset_id_migration
from db.migrations.add_total_score import run_total_score_migration
from db.migrations.add_avatar_cover_cache import run_avatar_cache_migration
from db.migrations.add_best_score_score import run_best_score_score_migration
from db.migrations.add_map_attempts import run_map_attempts_migration
from db.migrations.add_user_unlink_at import run_user_unlink_at_migration
from db.migrations.add_render_settings import run_render_settings_migration
from db.migrations.add_oauth_fields import run_oauth_migration
from db.migrations.add_bsk_tables import run_bsk_migration
from db.migrations.add_bsk_duels import run_bsk_duels_migration
from db.migrations.add_last_seen import run_last_seen_migration
from db.migrations.add_bsk_ml_runs import run_bsk_ml_runs_migration
from db.migrations.add_bsk_duel_test import run_bsk_duel_test_migration
from db.migrations.add_bsk_duel_overhaul import run_bsk_duel_overhaul_migration
from db.migrations.add_bsk_pause_ml_accuracy import run_bsk_pause_ml_accuracy_migration
from db.migrations.add_bsk_pick_phase import run_bsk_pick_phase_migration
from db.migrations.bsk_reset_calibration import run_bsk_reset_calibration_migration
from db.migrations.add_bsk_map_features import run_bsk_map_features_migration
from db.migrations.add_bsk_hp_drain import run_bsk_hp_drain_migration
from db.migrations.add_bsk_map_features_v2 import run_bsk_map_features_v2_migration
from tasks.bsk_ml_trainer import run_nightly_training
import db.models  # noqa: F401 — ensure all models registered for create_all

logger = get_logger(__name__)


class App:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.osu_api_client: Optional[OsuApiClient] = None
        self.shutdown_event = asyncio.Event()
        self.profile_updater_task: Optional[asyncio.Task] = None
        self.ml_trainer_task: Optional[asyncio.Task] = None
        self.duel_manager = None
        self.oauth_server: Optional[OAuthServer] = None

    async def setup(self) -> None:
        validate_settings()

        logger.info("Initializing bot and dispatcher...")
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher(storage=MemoryStorage())

        self.osu_api_client = OsuApiClient()

        # Middleware order: group restriction → rate limit → last seen → api client
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
        self.dp.include_router(bsk_router)

        logger.info("Checking/creating database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Running database migrations...")
        await run_migration(engine)
        await run_avatar_migration(engine)
        await run_beatmapset_id_migration(engine)
        await run_total_score_migration(engine)
        await run_avatar_cache_migration(engine)
        await run_best_score_score_migration(engine)
        await run_map_attempts_migration(engine)
        await run_user_unlink_at_migration(engine)
        await run_render_settings_migration(engine)
        await run_oauth_migration(engine)
        await run_bsk_migration(engine)
        await run_bsk_duels_migration(engine)
        await run_last_seen_migration(engine)
        await run_bsk_ml_runs_migration(engine)
        await run_bsk_duel_test_migration(engine)
        await run_bsk_duel_overhaul_migration(engine)
        await run_bsk_pause_ml_accuracy_migration(engine)
        await run_bsk_pick_phase_migration(engine)
        await run_bsk_reset_calibration_migration(engine)
        await run_bsk_map_features_migration(engine)
        await run_bsk_hp_drain_migration(engine)
        await run_bsk_map_features_v2_migration(engine)

        logger.info("Initializing osu! API client...")
        await self.osu_api_client.initialize()

        logger.info("Starting OAuth server...")
        self.oauth_server = OAuthServer()
        await self.oauth_server.start()
        oauth_set_bot(self.bot)
        init_duel_manager(self.bot, self.osu_api_client)

        logger.info("Starting background profile updater...")
        self.profile_updater_task = asyncio.create_task(
            periodic_profile_updates(self.osu_api_client, self.shutdown_event),
            name="profile_updater"
        )

        logger.info("Starting BSK ML nightly scheduler...")
        self.ml_trainer_task = asyncio.create_task(
            _nightly_ml_scheduler(self.shutdown_event),
            name="bsk_ml_scheduler"
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

        if self.ml_trainer_task:
            self.ml_trainer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.ml_trainer_task

        if self.oauth_server:
            await self.oauth_server.stop()

        if self.duel_manager:
            await self.duel_manager.stop()

        if self.osu_api_client:
            await self.osu_api_client.close()

        if self.bot:
            await self.bot.session.close()

        await close_shared_session()
        await close_engine()

        logger.info("Shutdown completed.")


async def _nightly_ml_scheduler(shutdown_event: asyncio.Event) -> None:
    """
    Runs BSK ML training once per night at 02:00 local time.
    Waits until 02:00, trains, then waits until next 02:00.
    """
    from datetime import datetime, timezone, timedelta

    while not shutdown_event.is_set():
        now = datetime.now()
        # Next 02:00
        target = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(f"BSK ML scheduler: next training in {wait_seconds/3600:.1f}h at {target.strftime('%H:%M')}")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=wait_seconds)
            break  # shutdown requested
        except asyncio.TimeoutError:
            pass  # time to train

        if shutdown_event.is_set():
            break

        logger.info("BSK ML nightly training starting...")
        try:
            result = await run_nightly_training()
            logger.info(f"BSK ML nightly training result: {result}")
        except Exception as e:
            logger.error(f"BSK ML nightly training failed: {e}", exc_info=True)


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
