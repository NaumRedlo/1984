# bot/main.py
"""
Bot Main Entry Point
Handles dispatcher, bot initialization, and background tasks.
"""

import asyncio
from aiogram import Bot, Dispatcher
from config.settings import TELEGRAM_BOT_TOKEN
from utils.logger import get_logger

# ← Initialize logger at start
logger = get_logger("main")
logger.info("Starting bot...")

from bot.handlers.start_handlers import router as start_router
from bot.handlers.auth_handlers import router as auth_router
from bot.handlers.profile_handlers import router as profile_router
from bot.handlers.hps_handlers import router as hps_router
from utils.osu_api_client import OsuApiClient
from bot.middlewares.api_client_middleware import ApiClientMiddleware
from db.models.user import Base
from db.database import engine, get_db_session
from tasks.profile_updater import periodic_profile_updates

osu_api_client_instance = None


async def main():
    global osu_api_client_instance
    
    logger.info("Checking/creating database tables...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Tables checked/created.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    
    logger.info("Initializing osu! API client...")
    try:
        osu_api_client_instance = OsuApiClient()
        await osu_api_client_instance.initialize()
        logger.info("✅ osu! API client initialized")
    except Exception as e:
        logger.critical(f"Failed to initialize osu! API client: {e}")
        raise
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    
    logger.info("Registering middlewares...")
    dp.message.middleware(ApiClientMiddleware(osu_api_client_instance))
    
    logger.info("Registering routers...")
    dp.include_router(start_router)
    dp.include_router(auth_router)
    dp.include_router(profile_router)
    dp.include_router(hps_router)
    
    logger.info("Starting background profile updater task...")
    asyncio.create_task(
        periodic_profile_updates(osu_api_client_instance, get_db_session)
    )
    
    logger.info("✅ Bot is up and running!")
    logger.info("Waiting for commands: /start, /register, /profile, /hps, /refresh")
    
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.warning("Bot stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.critical(f"Critical error during polling: {e}")
    finally:
        if osu_api_client_instance:
            await osu_api_client_instance.close()
            logger.info("osu! API client closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}")
        raise
