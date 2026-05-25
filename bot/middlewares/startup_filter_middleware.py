from datetime import datetime, timezone
from typing import Any, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from utils.logger import get_logger

logger = get_logger("middleware.startup_filter")


class StartupFilterMiddleware(BaseMiddleware):
    """Silently drop messages and callback queries that pre-date bot startup.

    Telegram queues updates while the bot is offline.  Even with
    drop_pending_updates=True in start_polling, there is a small window
    where stale updates can slip through.  This middleware closes that gap
    by comparing each event's timestamp against the moment the middleware
    was instantiated (i.e. when the bot process started).
    """

    def __init__(self) -> None:
        super().__init__()
        self._startup_time = datetime.now(timezone.utc)
        logger.info(f"StartupFilterMiddleware: ignoring events before {self._startup_time.isoformat()}")

    async def __call__(
        self,
        handler: Callable,
        event: Any,
        data: dict,
    ) -> Any:
        if isinstance(event, Message):
            msg_time = event.date
            if msg_time.tzinfo is None:
                msg_time = msg_time.replace(tzinfo=timezone.utc)
            if msg_time < self._startup_time:
                logger.debug(
                    f"Dropped stale message from user {event.from_user.id if event.from_user else '?'} "
                    f"(sent {msg_time.isoformat()})"
                )
                return

        elif isinstance(event, CallbackQuery):
            if event.message:
                msg_time = event.message.date
                if msg_time.tzinfo is None:
                    msg_time = msg_time.replace(tzinfo=timezone.utc)
                if msg_time < self._startup_time:
                    await event.answer("Устарело — повторите действие.", show_alert=False)
                    return

        return await handler(event, data)
