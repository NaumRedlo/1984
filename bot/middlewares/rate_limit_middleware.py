import time
from collections import defaultdict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any

from config.settings import ADMIN_IDS
from utils.logger import get_logger

logger = get_logger("middleware.rate_limit")

MAX_REQUESTS = 8
WINDOW_SECONDS = 10

class RateLimitMiddleware(BaseMiddleware):

    def __init__(self):
        super().__init__()

        self._requests: Dict[int, list] = defaultdict(list)

    def _is_limited(self, user_id: int) -> bool:
        now = time.monotonic()
        timestamps = self._requests[user_id]

        cutoff = now - WINDOW_SECONDS
        self._requests[user_id] = [t for t in timestamps if t > cutoff]
        timestamps = self._requests[user_id]

        if len(timestamps) >= MAX_REQUESTS:
            return True

        timestamps.append(now)
        return False

    async def __call__(
        self,
        handler: Callable,
        event: object,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
        else:
            return await handler(event, data)

        if not user_id:
            return await handler(event, data)

        if user_id in ADMIN_IDS:
            return await handler(event, data)

        if self._is_limited(user_id):
            logger.debug(f"Rate limited user {user_id}")
            if isinstance(event, CallbackQuery):
                await event.answer("Too many requests. Please wait.", show_alert=True)

            return

        return await handler(event, data)
