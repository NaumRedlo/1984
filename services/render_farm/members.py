from typing import Optional

from aiogram import Bot

async def groups_of(telegram_id: int) -> list[int]:
    from sqlalchemy import select

    from db.database import AsyncSessionFactory
    from db.models import User

    async with AsyncSessionFactory() as session:
        found = await session.execute(
            select(User.chat_id).where(User.telegram_id == telegram_id, User.chat_id < 0).distinct()
        )
        return [row[0] for row in found.all()]

async def is_member(bot: Optional[Bot], chat_id: int, telegram_id: int) -> bool:
    if bot is None:
        return False
    try:
        member = await bot.get_chat_member(chat_id, telegram_id)
    except Exception:
        return False
    return getattr(member, "status", "left") not in {"left", "kicked"}

async def shares_a_group(bot: Optional[Bot], telegram_id: int) -> bool:
    for group in await groups_of(telegram_id):
        if await is_member(bot, group, telegram_id):
            return True
    return False
