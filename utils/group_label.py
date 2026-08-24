from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_LABEL_CACHE: dict[int, str] = {}


async def group_label(bot, chat_id: int) -> str:
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
