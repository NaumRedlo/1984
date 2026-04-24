from datetime import datetime, timezone
from typing import Callable, Dict, Any

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import update

from db.database import AsyncSessionFactory
from db.models.user import User
from utils.logger import get_logger

logger = get_logger("middleware.last_seen")

# Update at most once per 5 minutes per user to avoid DB spam
_COOLDOWN_SECONDS = 300
_last_updated: Dict[int, float] = {}


class LastSeenMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: object, data: Dict[str, Any]) -> Any:
        import time

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
        else:
            return await handler(event, data)

        if user_id:
            now_mono = time.monotonic()
            if now_mono - _last_updated.get(user_id, 0) > _COOLDOWN_SECONDS:
                _last_updated[user_id] = now_mono
                try:
                    async with AsyncSessionFactory() as session:
                        await session.execute(
                            update(User)
                            .where(User.telegram_id == user_id)
                            .values(last_seen_at=datetime.now(timezone.utc))
                        )
                        await session.commit()
                except Exception as e:
                    logger.debug(f"last_seen update failed for {user_id}: {e}")

        return await handler(event, data)
