from typing import Optional

from aiogram import Bot

from utils.ttl_cache import TTLCache

MEMBER_KEEP = 120.0
LEFT_KEEP = 20.0

_inside = TTLCache(maxsize=4000, ttl=MEMBER_KEEP)
_outside = TTLCache(maxsize=4000, ttl=LEFT_KEEP)

def forget() -> None:
    _inside.clear()
    _outside.clear()

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
    key = (chat_id, telegram_id)
    if key in _inside:
        return True
    if key in _outside:
        return False
    try:
        member = await bot.get_chat_member(chat_id, telegram_id)
    except Exception:
        return False
    inside = getattr(member, "status", "left") not in {"left", "kicked"}
    (_inside if inside else _outside)[key] = True
    return inside

async def shares_a_group(bot: Optional[Bot], telegram_id: int) -> bool:
    for group in await groups_of(telegram_id):
        if await is_member(bot, group, telegram_id):
            return True
    return False
