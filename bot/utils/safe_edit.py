from __future__ import annotations

from typing import Any

from aiogram.exceptions import TelegramBadRequest

_BENIGN_EDIT_MARKERS = (
    "message is not modified",
    "canceled by new",
)

def is_benign_edit_race(exc: BaseException) -> bool:
    if not isinstance(exc, TelegramBadRequest):
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _BENIGN_EDIT_MARKERS)

async def safe_edit_media(message: Any, **kwargs: Any) -> bool:
    try:
        await message.edit_media(**kwargs)
        return True
    except TelegramBadRequest as e:
        if is_benign_edit_race(e):
            return False
        raise

async def safe_edit_text(message: Any, text: str, **kwargs: Any) -> bool:
    try:
        await message.edit_text(text, **kwargs)
        return True
    except TelegramBadRequest as e:
        if is_benign_edit_race(e):
            return False
        raise

__all__ = ["is_benign_edit_race", "safe_edit_media", "safe_edit_text"]
