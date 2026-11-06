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

from services import incidents
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
    await _note(event, exc, where)
    return False

def _asked(message: Message) -> bool:
    return message.chat.type == "private" or message.document is not None or (message.text or "").lstrip().startswith("/")

async def _note(event: ErrorEvent, exc: BaseException, where: str) -> None:
    upd = event.update
    message = upd.message if upd is not None and isinstance(upd.message, Message) else None
    callback = upd.callback_query if upd is not None and isinstance(upd.callback_query, CallbackQuery) else None
    person = (message.from_user if message is not None else callback.from_user if callback is not None else None)
    chat = message.chat.id if message is not None else None
    detail = {"where": where}
    if message is not None and message.document is not None:
        detail["file"] = message.document.file_name
        detail["size"] = message.document.file_size
    try:
        code = await incidents.note("bot", "handler", f"{type(exc).__name__}: {exc}", telegram_id=person.id if person else None,
                                    chat_id=chat, detail=detail, error=exc)
    except Exception as again:
        logger.warning(f"the incident was not noted [{where}]: {again}")
        return
    try:
        lang = (await get_language(person.id)).lower() if person else "en"
        if callback is not None:
            await callback.answer(t("common.broken_code", lang, code=code), show_alert=True)
        elif message is not None and _asked(message):
            await message.reply(t("common.broken_code", lang, code=code))
    except Exception as again:
        logger.info(f"could not tell about incident {code} [{where}]: {again}")

__all__ = ["router"]
