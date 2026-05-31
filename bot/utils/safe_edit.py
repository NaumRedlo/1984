"""Telegram edit-message helpers that swallow benign races.

Telegram returns 400 for two common no-op conditions on `editMessageMedia`
and `editMessageText`:

  * "message is not modified"
      The new content is byte-identical to the old. Typical on rapid
      double-clicks or when a callback re-fetches the same page.

  * "canceled by new editMessageMedia request"
      The user clicked another button before this edit completed. Telegram
      drops this request in favor of the later one; the later one is what
      actually reaches the screen.

Both cases mean "user intent is already served, do nothing." Logging them
at ERROR pollutes logs and confuses on-call. Use these helpers anywhere a
single user can trigger overlapping edits (paginated leaderboards, profile
panels, help carousels, bounty/duel cards).
"""

from __future__ import annotations

from typing import Any

from aiogram.exceptions import TelegramBadRequest


# Messages Telegram returns for benign races on editMessage* methods.
# Match substrings — Telegram occasionally tweaks the exact wording.
_BENIGN_EDIT_MARKERS = (
    "message is not modified",
    "canceled by new",        # "canceled by new editMessageMedia request"
)


def is_benign_edit_race(exc: BaseException) -> bool:
    """True if `exc` is a TelegramBadRequest from a benign concurrent edit."""
    if not isinstance(exc, TelegramBadRequest):
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _BENIGN_EDIT_MARKERS)


async def safe_edit_media(message: Any, **kwargs: Any) -> bool:
    """`message.edit_media(**kwargs)` that swallows benign races.

    Returns True if the edit succeeded, False if it was dropped as a benign
    race. Any non-benign `TelegramBadRequest` is re-raised unchanged so
    real errors still bubble up to the caller's logger.
    """
    try:
        await message.edit_media(**kwargs)
        return True
    except TelegramBadRequest as e:
        if is_benign_edit_race(e):
            return False
        raise


async def safe_edit_text(message: Any, text: str, **kwargs: Any) -> bool:
    """`message.edit_text(text, **kwargs)` that swallows benign races.

    Same contract as `safe_edit_media`.
    """
    try:
        await message.edit_text(text, **kwargs)
        return True
    except TelegramBadRequest as e:
        if is_benign_edit_race(e):
            return False
        raise


__all__ = ["is_benign_edit_race", "safe_edit_media", "safe_edit_text"]
