import time
from collections import deque
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any

from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.ttl_cache import TTLCache

logger = get_logger("middleware.rate_limit")

MAX_REQUESTS = 5
WINDOW_SECONDS = 10
COMMAND_INTERVAL = 1.5
CALLBACK_INTERVAL = 0.6

class RateLimitMiddleware(BaseMiddleware):

    def __init__(self):
        super().__init__()

        clock = lambda: time.monotonic()
        self._requests = TTLCache(maxsize=20000, ttl=WINDOW_SECONDS, clock=clock)
        self._notified = TTLCache(maxsize=20000, ttl=3, clock=clock)
        self._running: set[int] = set()

    def _is_limited(self, user_id: int, interval: float) -> bool:
        now = time.monotonic()
        timestamps = self._requests.get(user_id, deque())
        cutoff = now - WINDOW_SECONDS
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
        if len(timestamps) >= MAX_REQUESTS or (timestamps and now - timestamps[-1] < interval):
            return True
        timestamps.append(now)
        self._requests[user_id] = timestamps
        return False

    async def _reject(self, event, user_id: int, busy: bool) -> None:
        if user_id in self._notified:
            if isinstance(event, CallbackQuery):
                await event.answer()
            return
        self._notified[user_id] = True
        lang = (await get_language(user_id)).lower()
        text = t("common.command_running", lang) if busy else t("common.commands_too_fast", lang)
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=False)
        else:
            await event.answer(text)

    async def __call__(
        self,
        handler: Callable,
        event: object,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if not event.text or not (data.get("trigger_args") or data.get("command") or event.text.lstrip().startswith("/")):
                return await handler(event, data)
            user_id = event.from_user.id if event.from_user else None
            interval = COMMAND_INTERVAL
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            interval = CALLBACK_INTERVAL
        else:
            return await handler(event, data)

        if not user_id:
            return await handler(event, data)

        busy = user_id in self._running
        if busy or self._is_limited(user_id, interval):
            logger.debug(f"Rate limited user {user_id}")
            await self._reject(event, user_id, busy)
            return
        self._running.add(user_id)
        try:
            return await handler(event, data)
        finally:
            self._running.discard(user_id)
