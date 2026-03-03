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
    async for session in get_db_session():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

@router.message(Command("register"))
async def register_user(message: types.Message, osu_api_client):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.answer(
            "Format: `/register <nickname>`\nExample: `/register nazeetskyyy`",
            parse_mode="Markdown"
        )

    raw_username = args[1].strip()
    tg_id = message.from_user.id

    user_data = await osu_api_client.get_user_by_name(raw_username)
    if not user_data or not user_data.get("id"):
        return await message.answer(
            f"User **{raw_username}** not found in osu!.",
            parse_mode="Markdown"
        )

    osu_id = user_data["id"]
    osu_name = user_data["username"]

    async with get_db_session() as session:
        stmt = select(User).where(User.telegram_id == tg_id)
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            return await message.answer(
                f"You are already registered as **{existing.osu_username}** (ID {existing.osu_user_id})",
                parse_mode="Markdown"
            )

        stmt_dupe = select(User).where(User.osu_user_id == osu_id)
        dupe = (await session.execute(stmt_dupe)).scalar_one_or_none()
        if dupe:
            return await message.answer(
                f"This osu! account (**{osu_name}**) is already linked to another Telegram account.",
                parse_mode="Markdown"
            )

        new_user = User(
            telegram_id=tg_id,
            osu_username=osu_name,
            osu_user_id=osu_id,
        )
        session.add(new_user)

    await osu_api_client.update_user_in_db(session, new_user)

    await message.answer(
        f"Registration was successful!\n"
        f"osu!: **{osu_name}** (ID {osu_id})\n"
        f"Ready to hunt for bounties, Candidate.",
        parse_mode="Markdown"
    )

    async for session in get_db_session():
        try:
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            existing_user = result.scalar_one_or_none()

            if existing_user:
                await message.answer(
                    f"You are already registered in the system.\n"
                    f"Telegram: `{message.from_user.full_name}`\n"
                    f"osu!: `{existing_user.osu_username}` (ID: {existing_user.osu_user_id})\n"
                    f"HPS: {existing_user.hps_points} HP\n"
                    f"Rank: {existing_user.rank}",
                    parse_mode="Markdown"
                )
                return

            new_user = User(
                telegram_id=tg_id,
                osu_username=osu_username_provided,
                osu_user_id=user_data.get('id'),
            )
            session.add(new_user)
            await session.commit()

            await message.answer(
                f"Registration in the system was successful!\n"
                f"Telegram: `{message.from_user.full_name}`\n"
                f"osu!: `{osu_username_provided}` (ID: {user_data.get('id')})\n"
                f"HPS: {new_user.hps_points} HP\n"
                f"Rank: {new_user.rank}",
                parse_mode="Markdown"
            )
            return

        except Exception as e:
            await session.rollback()
            logger.error(f"Error processing /register for {tg_id}: {e}")
            await message.answer(
                "An error occurred during registration. Please try again later."
            )
            raise

__all__ = ["router"]
