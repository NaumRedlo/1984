"""
Global aiogram error handler.

Catches TelegramAPIError that propagates from any handler (Message,
CallbackQuery, etc.) and logs it instead of letting it bubble up to
asyncio "unhandled exception" noise.

We always return True ("error handled") for known Telegram errors so the
update is considered processed. Real (non-Telegram) bugs are re-raised
so they remain visible in the logs and alerting.
"""

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
from utils.telegram_safe import _BENIGN_BAD_REQUESTS  # noqa: F401 - reuse list

logger = get_logger("handlers.errors")

router = Router(name="errors")


def _benign(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return any(p in text for p in _BENIGN_BAD_REQUESTS)


def _describe_event(event: ErrorEvent) -> str:
    """Produce a short identifier of where the error happened."""
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
    """Return True to mark the update as handled (no re-raise)."""
    exc = event.exception
    where = _describe_event(event)

    # ── Benign Telegram BadRequests — debug-level log, swallow ───────────────
    if isinstance(exc, TelegramBadRequest) and _benign(exc):
        logger.debug(f"BadRequest (benign) [{where}]: {exc}")
        return True

    # ── Other Telegram BadRequests — warning, swallow ────────────────────────
    if isinstance(exc, TelegramBadRequest):
        logger.warning(f"BadRequest [{where}]: {exc}")
        # Try to ack the callback so the user doesn't see the spinner forever.
        cb = event.update.callback_query if event.update else None
        if cb is not None:
            try:
                lang = (await get_language(cb.from_user.id)).lower() if cb.from_user else "en"
                await cb.answer(t("common.something_wrong", lang), show_alert=False)
            except TelegramAPIError:
                pass
        return True

    # ── User blocked the bot / kicked from chat — info log, swallow ─────────
    if isinstance(exc, TelegramForbiddenError):
        logger.info(f"Forbidden [{where}]: {exc}")
        return True

    # ── Flood-wait — warn and let aiogram's built-in retry handle it ────────
    if isinstance(exc, TelegramRetryAfter):
        logger.warning(f"RetryAfter [{where}]: {exc.retry_after}s")
        return True

    # ── Network blip — warn, swallow (next poll will recover) ───────────────
    if isinstance(exc, TelegramNetworkError):
        logger.warning(f"Network error [{where}]: {exc}")
        return True

    # ── Any other TelegramAPIError — log full traceback, swallow ────────────
    if isinstance(exc, TelegramAPIError):
        logger.error(f"TelegramAPIError [{where}]: {exc}", exc_info=True)
        return True

    # ── Anything else — log with traceback but DON'T swallow.                ─
    # Returning False lets aiogram re-raise so the bug stays visible.
    logger.error(f"Unhandled exception [{where}]: {exc}", exc_info=True)
    return False


__all__ = ["router"]
