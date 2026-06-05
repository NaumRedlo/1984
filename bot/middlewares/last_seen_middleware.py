from datetime import datetime, timezone
from typing import Callable, Dict, Any

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import update

from db.database import AsyncSessionFactory
from db.models.user import User
from utils.logger import get_logger

logger = get_logger("middleware.last_seen")

# Update at most once per 5 minutes per (user, group) to avoid DB spam.
_COOLDOWN_SECONDS = 300
_last_updated: Dict[tuple[int, int], float] = {}


def _event_chat(event) -> object | None:
    if isinstance(event, Message):
        return event.chat
    if isinstance(event, CallbackQuery):
        return event.message.chat if event.message else None
    return None


class LastSeenMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: object, data: Dict[str, Any]) -> Any:
        import time

        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        chat = _event_chat(event)
        # last_seen is per-tenant: only group activity counts (it feeds duel
        # "online" detection). DMs have no group row, so they're skipped.
        chat_id = chat.id if chat and chat.type in ("group", "supergroup") else None

        if user_id and chat_id is not None:
            now_mono = time.monotonic()
            key = (user_id, chat_id)
            if now_mono - _last_updated.get(key, 0) > _COOLDOWN_SECONDS:
                _last_updated[key] = now_mono
                try:
                    async with AsyncSessionFactory() as session:
                        await session.execute(
                            update(User)
                            .where(User.telegram_id == user_id, User.chat_id == chat_id)
                            .values(last_seen_at=datetime.now(timezone.utc))
                        )
                        await session.commit()
                except Exception as e:
                    logger.debug(f"last_seen update failed for {user_id}@{chat_id}: {e}")

        return await handler(event, data)
