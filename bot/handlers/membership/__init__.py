from aiogram import F, Router, types

from services import membership
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="membership")

@router.message(F.left_chat_member)
async def someone_left(message: types.Message):
    who = message.left_chat_member
    if who is None or who.is_bot or message.chat.type not in ("group", "supergroup"):
        return
    from db.database import AsyncSessionFactory

    try:
        await membership.gone(AsyncSessionFactory, message.chat.id, who.id)
    except Exception as exc:
        logger.warning("could not take %s out of chat %s: %s", who.id, message.chat.id, exc)

@router.message(F.new_chat_members)
async def someone_came(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    from db.database import AsyncSessionFactory

    for who in message.new_chat_members or []:
        if who.is_bot:
            continue
        try:
            await membership.came(AsyncSessionFactory, message.chat.id, who.id)
        except Exception as exc:
            logger.warning("could not put %s into chat %s: %s", who.id, message.chat.id, exc)
