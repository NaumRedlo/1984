from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.models.user import User
from db.database import get_db_session
from contextlib import asynccontextmanager

router = Router()

@asynccontextmanager
async def get_session():
    """
    Context manager for obtaining a database session.
    Provides commit on success and rollback on error.
    """
    async for session in get_db_session():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

@router.message(Command("register"))
async def register_user(message: types.Message, **kwargs):
    """
    Processor of the command /register <nickname>.
    - Checks whether the user is already registered by telegram_id.
    - Checks the existence of an osu! user via the API.
    - Creates or updates a record in the database.
    - Sends a confirmation message to the user.
    """
    # === 1. Get api_client from kwargs passed by middleware ===
    api_client = kwargs.get("osu_api_client")
    if not api_client:
        await message.answer("❌ Error: API client not initialized. Please try again later.")
        return

    # === 2. Parsing the command argument ===
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Incorrect command format.\n"
            "Use: `/register <nickname>`",
            parse_mode="Markdown"
        )
        return

    osu_username_provided = args[1].strip()
    tg_id = message.from_user.id

    # === 3. Verifying users via the osu! API ===
    user_data = await api_client.get_user_by_name(osu_username_provided)
    if not user_data:
        await message.answer(
            f"❌ User `{osu_username_provided}` not found in osu!.",
            parse_mode="Markdown"
        )
        return

    # === 4. Working with the database ===
    async for session in get_db_session():
        try:
            # === 4.1. Check if the user exists in the database by telegram_id ===
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            existing_user = result.scalar_one_or_none()

            if existing_user:
                await message.answer(
                    f"✅ You are already registered in the system.\n"
                    f"Telegram: `{message.from_user.full_name}`\n"
                    f"osu!: `{existing_user.osu_username}` (ID: {existing_user.osu_user_id})\n"
                    f"HPS: {existing_user.hps_points} HP\n"
                    f"Rank: {existing_user.rank}",
                    parse_mode="Markdown"
                )
                return

            # === 4.2. Creating a new user ===
            new_user = User(
                telegram_id=tg_id,
                osu_username=osu_username_provided,
                osu_user_id=user_data.get('id'),
            )
            session.add(new_user)
            await session.commit()

            # === 4.3. Sending confirmation to the user ===
            await message.answer(
                f"✅ Registration in the system was successful!\n"
                f"Telegram: `{message.from_user.full_name}`\n"
                f"osu!: `{osu_username_provided}` (ID: {user_data.get('id')})\n"
                f"HPS: {new_user.hps_points} HP\n"
                f"Rank: {new_user.rank}",
                parse_mode="Markdown"
            )
            return

        except Exception as e:
            # === 5. Error handling ===
            await session.rollback()
            # Log the error to the console
            print(f"Error processing /register for {tg_id}: {e}")
            # Sending the user an error message
            await message.answer(
                "❌ An error occurred during registration. Please try again later."
            )
            raise

__all__ = ["router"]
