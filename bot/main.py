import asyncio
from aiogram import Bot, Dispatcher
from config.settings import TELEGRAM_BOT_TOKEN
from bot.handlers.start_handlers import router as start_router
from bot.handlers.auth_handlers import router as auth_router
from bot.handlers.profile_handlers import router as profile_router
from utils.osu_api_client import OsuApiClient
from bot.middlewares.api_client_middleware import ApiClientMiddleware
from db.models.user import Base
from db.database import engine

# Global variable for the client
osu_api_client_instance = None

async def main():
    global osu_api_client_instance

    # === Creating tables ===
    print("Checking/creating tables in the database...")
    async with engine.begin() as conn:
        # Creating tables based on specific models (Base)
        await conn.run_sync(Base.metadata.create_all)
    print("Tables checked/created.")
    # === End of table creation ===

    # Initialize the client
    osu_api_client_instance = OsuApiClient()
    await osu_api_client_instance.initialize()

    # Initialization of the bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # Registering middleware for message handlers
    # This will pass osu_api_client to the data handler.
    dp.message.middleware(ApiClientMiddleware(osu_api_client_instance))

    # Registering routers
    dp.include_router(start_router)
    dp.include_router(auth_router)
    dp.include_router(profile_router)

    print("✅ The bot is up and running. Waiting for commands /start, /register, /profile...")

    try:
        await dp.start_polling(bot)
    finally:
        # Closing the client session at completion
        if osu_api_client_instance:
            await osu_api_client_instance.close()

if __name__ == "__main__":
    asyncio.run(main())

