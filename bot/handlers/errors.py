from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import CallbackQuery, ErrorEvent, Message

from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.telegram_safe import _BENIGN_BAD_REQUESTS

logger = get_logger("handlers.errors")

router = Router(name="errors")

def _benign(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return any(p in text for p in _BENIGN_BAD_REQUESTS)

def _describe_event(event: ErrorEvent) -> str:
    upd = event.update
    if upd is None:
        return "update=?"
    parts = [f"update_id={upd.update_id}"]

    if isinstance(upd.message, Message):
        m = upd.message
        parts.append(f"chat={m.chat.id}")
        if m.from_user:
            parts.append(f"user={m.from_user.id}")
        if m.text:
            parts.append(f"text={m.text[:40]!r}")
    elif isinstance(upd.callback_query, CallbackQuery):
        cb = upd.callback_query
        if cb.from_user:
            parts.append(f"user={cb.from_user.id}")
        if cb.data:
            parts.append(f"data={cb.data!r}")
        if cb.message and hasattr(cb.message, "chat"):
            parts.append(f"chat={cb.message.chat.id}")

    return " ".join(parts)

@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    exc = event.exception
    where = _describe_event(event)

    if isinstance(exc, TelegramBadRequest) and _benign(exc):
        logger.debug(f"BadRequest (benign) [{where}]: {exc}")
        return True

    if isinstance(exc, TelegramBadRequest):
        logger.warning(f"BadRequest [{where}]: {exc}")

        cb = event.update.callback_query if event.update else None
        if cb is not None:
            try:
                lang = (await get_language(cb.from_user.id)).lower() if cb.from_user else "en"
                await cb.answer(t("common.something_wrong", lang), show_alert=False)
            except TelegramAPIError:
                pass
        return True

    if isinstance(exc, TelegramForbiddenError):
        logger.info(f"Forbidden [{where}]: {exc}")
        return True

    if isinstance(exc, TelegramRetryAfter):
        logger.warning(f"RetryAfter [{where}]: {exc.retry_after}s")
        return True

    if isinstance(exc, TelegramNetworkError):
        logger.warning(f"Network error [{where}]: {exc}")
        return True

    if isinstance(exc, TelegramAPIError):
        logger.error(f"TelegramAPIError [{where}]: {exc}", exc_info=True)
        return True

    logger.error(f"Unhandled exception [{where}]: {exc}", exc_info=True)
    return False

__all__ = ["router"]
