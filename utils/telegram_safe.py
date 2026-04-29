"""
Safe wrappers around aiogram Bot calls.

Telegram regularly throws BadRequest for absolutely benign reasons:
  - "message is not modified"
  - "message to edit not found"
  - "message to delete not found"
  - "message can't be edited"
  - "query is too old"
  - "chat not found" (user opened DM and left, etc.)

These errors should not crash background tasks. The helpers below catch
the well-known ignorable errors, log everything else, and never raise
TelegramAPIError to the caller.

Usage:
    from utils.telegram_safe import (
        safe_edit_text, safe_edit_caption, safe_edit_reply_markup,
        safe_edit_message_media, safe_send_message, safe_send_photo,
        safe_delete_message, safe_answer_callback, suppress_telegram_errors,
    )
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import CallbackQuery

from utils.logger import get_logger

logger = get_logger("telegram_safe")

# Substrings of error messages that are safe to silently ignore.
# Compared case-insensitively against str(exc).
_BENIGN_BAD_REQUESTS: tuple[str, ...] = (
    "message is not modified",
    "message to edit not found",
    "message to delete not found",
    "message to reply not found",
    "message can't be edited",
    "message can't be deleted",
    "query is too old",
    "query id is invalid",
    "response_url_invalid",
    "chat not found",
    "user not found",
    "have no rights to send a message",
    "not enough rights",
    "bot was blocked by the user",
    "bot can't initiate conversation",
    "bot is not a member",
    "user is deactivated",
    "peer_id_invalid",
    "wrong file_id",
    "there is no caption in the message to edit",
    "message_id_invalid",
)


def _is_benign(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return any(p in text for p in _BENIGN_BAD_REQUESTS)


def _log_telegram_error(operation: str, exc: BaseException) -> None:
    """Centralized logging for Telegram errors."""
    if isinstance(exc, TelegramBadRequest):
        if _is_benign(exc):
            logger.debug(f"[{operation}] benign BadRequest: {exc}")
        else:
            logger.warning(f"[{operation}] TelegramBadRequest: {exc}")
    elif isinstance(exc, TelegramForbiddenError):
        logger.info(f"[{operation}] Forbidden (user blocked bot / kicked): {exc}")
    elif isinstance(exc, TelegramRetryAfter):
        logger.warning(
            f"[{operation}] flood-wait: retry after {exc.retry_after}s"
        )
    elif isinstance(exc, TelegramNetworkError):
        logger.warning(f"[{operation}] network error: {exc}")
    elif isinstance(exc, TelegramAPIError):
        logger.error(f"[{operation}] TelegramAPIError: {exc}")
    else:
        logger.error(f"[{operation}] unexpected error: {exc}", exc_info=True)


@asynccontextmanager
async def suppress_telegram_errors(operation: str = "telegram_call"):
    """Async context manager that swallows TelegramAPIError + logs nicely."""
    try:
        yield
    except TelegramAPIError as exc:
        _log_telegram_error(operation, exc)
    except Exception as exc:  # pragma: no cover - defensive
        # Don't swallow CancelledError / KeyboardInterrupt.
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _log_telegram_error(operation, exc)


# ── Thin wrappers over Bot methods ────────────────────────────────────────────


async def safe_edit_text(
    bot: Bot,
    text: str,
    *,
    chat_id: int,
    message_id: int,
    **kwargs: Any,
) -> bool:
    try:
        await bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id, **kwargs
        )
        return True
    except TelegramAPIError as exc:
        _log_telegram_error("edit_message_text", exc)
        return False


async def safe_edit_caption(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    caption: Optional[str] = None,
    **kwargs: Any,
) -> bool:
    try:
        await bot.edit_message_caption(
            chat_id=chat_id, message_id=message_id, caption=caption, **kwargs
        )
        return True
    except TelegramAPIError as exc:
        _log_telegram_error("edit_message_caption", exc)
        return False


async def safe_edit_reply_markup(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    reply_markup: Any = None,
) -> bool:
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
        )
        return True
    except TelegramAPIError as exc:
        _log_telegram_error("edit_message_reply_markup", exc)
        return False


async def safe_edit_message_media(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    media: Any,
    reply_markup: Any = None,
) -> bool:
    try:
        await bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=media,
            reply_markup=reply_markup,
        )
        return True
    except TelegramAPIError as exc:
        _log_telegram_error("edit_message_media", exc)
        return False


async def safe_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    **kwargs: Any,
):
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except TelegramAPIError as exc:
        _log_telegram_error("send_message", exc)
        return None


async def safe_send_photo(bot: Bot, chat_id: int, photo: Any, **kwargs: Any):
    try:
        return await bot.send_photo(chat_id, photo=photo, **kwargs)
    except TelegramAPIError as exc:
        _log_telegram_error("send_photo", exc)
        return None


async def safe_delete_message(bot: Bot, chat_id: int, message_id: int) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except TelegramAPIError as exc:
        _log_telegram_error("delete_message", exc)
        return False


async def safe_answer_callback(
    callback: CallbackQuery,
    text: Optional[str] = None,
    *,
    show_alert: bool = False,
    **kwargs: Any,
) -> bool:
    try:
        await callback.answer(text=text, show_alert=show_alert, **kwargs)
        return True
    except TelegramAPIError as exc:
        _log_telegram_error("answer_callback_query", exc)
        return False


__all__ = [
    "suppress_telegram_errors",
    "safe_edit_text",
    "safe_edit_caption",
    "safe_edit_reply_markup",
    "safe_edit_message_media",
    "safe_send_message",
    "safe_send_photo",
    "safe_delete_message",
    "safe_answer_callback",
]
