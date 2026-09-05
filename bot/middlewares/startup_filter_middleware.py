from datetime import datetime, timezone
from typing import Any, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger

logger = get_logger("middleware.startup_filter")

class StartupFilterMiddleware(BaseMiddleware):

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
                    lang = (await get_language(event.from_user.id)).lower() if event.from_user else "en"
                    await event.answer(t("common.stale_repeat", lang), show_alert=False)
                    return

        return await handler(event, data)
