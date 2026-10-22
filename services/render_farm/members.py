import json
from typing import Any, Optional

from aiogram import Bot
from aiogram.exceptions import ClientDecodeError

from utils.logger import get_logger
from utils.ttl_cache import TTLCache

logger = get_logger("render_farm.members")

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

def _raw_status(data: Any) -> Optional[str]:
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    result = data.get("result", data)
    status = result.get("status") if isinstance(result, dict) else None
    return status if isinstance(status, str) else None

async def is_member(bot: Optional[Bot], chat_id: int, telegram_id: int) -> bool:
    if bot is None:
        logger.warning("membership of %s in %s is unknown: the bot is not set yet", telegram_id, chat_id)
        return False
    key = (chat_id, telegram_id)
    if key in _inside:
        return True
    if key in _outside:
        return False
    try:
        member = await bot.get_chat_member(chat_id, telegram_id)
        status = getattr(member, "status", "left")
    except ClientDecodeError as exc:
        status = _raw_status(exc.data)
        if status is None:
            logger.warning("membership of %s in %s was not read: %s", telegram_id, chat_id, exc.original)
            return False
    except Exception as exc:
        logger.warning("membership of %s in %s was not checked: %s: %s", telegram_id, chat_id, type(exc).__name__, exc)
        return False
    inside = str(status) not in {"left", "kicked"}
    (_inside if inside else _outside)[key] = True
    return inside

async def shares_a_group(bot: Optional[Bot], telegram_id: int) -> bool:
    for group in await groups_of(telegram_id):
        if await is_member(bot, group, telegram_id):
            return True
    return False
