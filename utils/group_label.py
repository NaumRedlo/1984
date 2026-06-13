"""Human-readable labels for Telegram group chat_ids, cached in-memory.

The DM group-picker shows the *names* of the groups a user is registered in, but
the bot only stores raw ``chat_id``s. We resolve titles lazily via
``bot.get_chat`` and cache them for the process lifetime — titles change rarely
and a stale title is harmless. Falls back to the numeric id if the chat can't be
fetched (bot removed from the group, etc.).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_LABEL_CACHE: dict[int, str] = {}


async def group_label(bot, chat_id: int) -> str:
    """Title of the group ``chat_id`` (cached), or its id as a string fallback."""
    cached = _LABEL_CACHE.get(chat_id)
    if cached is not None:
        return cached
    try:
        chat = await bot.get_chat(chat_id)
        title = (getattr(chat, "title", None) or getattr(chat, "full_name", None)
                 or str(chat_id))
    except Exception as e:
        logger.debug(f"group_label: get_chat({chat_id}) failed: {e}")
        return str(chat_id)
    _LABEL_CACHE[chat_id] = title
    return title
