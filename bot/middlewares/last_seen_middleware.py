import asyncio
from datetime import timezone
from typing import Callable, Dict, Any
from weakref import WeakValueDictionary

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from db.database import AsyncSessionFactory
from db.models.user import User
from utils.logger import get_logger
from utils.timeutils import utcnow
from utils.ttl_cache import TTLCache
from utils.title_progress import detect_comeback, touch_activity_day, unlock_title

logger = get_logger("middleware.last_seen")

_COOLDOWN_SECONDS = 300
_last_updated = TTLCache(maxsize=20000, ttl=_COOLDOWN_SECONDS)
_activity_locks = WeakValueDictionary()

def _event_chat(event) -> object | None:
    if isinstance(event, Message):
        return event.chat
    if isinstance(event, CallbackQuery):
        return event.message.chat if event.message else None
    return None

class LastSeenMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: object, data: Dict[str, Any]) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user and not event.from_user.is_bot else None
        chat = _event_chat(event)

        chat_id = chat.id if chat and chat.type in ("group", "supergroup") else None

        if user_id and chat_id is not None:
            moment = event.date if isinstance(event, Message) else utcnow()
            if moment.tzinfo is not None:
                moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
            key = (user_id, chat_id, moment.date())
            lock = _activity_locks.setdefault(user_id, asyncio.Lock())
            async with lock:
                if key not in _last_updated:
                    try:
                        async with AsyncSessionFactory() as session:
                            user = (await session.execute(
                                select(User).where(
                                    User.telegram_id == user_id, User.chat_id == chat_id)
                            )).scalar_one_or_none()
                            if user is not None:
                                came_back = detect_comeback(user)
                                touch_activity_day(user, moment.date())
                                user.last_seen_at = max(user.last_seen_at or moment, moment)
                                if came_back:
                                    await unlock_title(user, "comeback_180d", session)
                                await session.commit()
                                _last_updated[key] = True
                    except Exception as e:
                        logger.warning(f"last_seen update failed for {user_id}@{chat_id}: {e}")

        return await handler(event, data)
