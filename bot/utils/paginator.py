"""In-memory page cache + navigation keyboard for inline-button pagination.

Usage:
    pages = build_pages(lines, max_chars=3800)
    store_pages("bli", user_id, pages)
    keyboard = nav_keyboard("bli", user_id, page=0, total=len(pages))
    await message.answer(pages[0], reply_markup=keyboard, parse_mode="HTML")

Callback data format:  pg|<prefix>|<user_id>|<page_index>
Fits in Telegram's 64-byte callback_data limit for realistic prefixes.
"""

from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_PAGE_CACHE: dict[str, dict] = {}
_TTL = timedelta(minutes=10)
_MAX_CHARS = 3800


def build_pages(lines: list[str], max_chars: int = _MAX_CHARS) -> list[str]:
    """Split `lines` into pages that each fit within `max_chars`."""
    pages: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        cost = len(line) + 1  # +1 for newline
        if current and current_len + cost > max_chars:
            pages.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += cost

    if current:
        pages.append("\n".join(current))

    return pages or [""]


def store_pages(prefix: str, user_id: int, pages: list[str]) -> None:
    key = f"{prefix}:{user_id}"
    _PAGE_CACHE[key] = {
        "pages": pages,
        "expires_at": datetime.utcnow() + _TTL,
    }


def get_pages(prefix: str, user_id: int) -> list[str] | None:
    key = f"{prefix}:{user_id}"
    entry = _PAGE_CACHE.get(key)
    if not entry:
        return None
    if datetime.utcnow() > entry["expires_at"]:
        del _PAGE_CACHE[key]
        return None
    return entry["pages"]


def nav_keyboard(
    prefix: str, user_id: int, page: int, total: int
) -> InlineKeyboardMarkup | None:
    """Return navigation keyboard, or None when there's only one page."""
    if total <= 1:
        return None

    def cb(p: int) -> str:
        return f"pg|{prefix}|{user_id}|{p}"

    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="◀", callback_data=cb(page - 1)))
    buttons.append(
        InlineKeyboardButton(text=f"{page + 1} / {total}", callback_data="pg|noop")
    )
    if page < total - 1:
        buttons.append(InlineKeyboardButton(text="▶", callback_data=cb(page + 1)))

    return InlineKeyboardMarkup(inline_keyboard=[buttons])
