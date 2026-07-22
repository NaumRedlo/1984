"""Proactive request notifications.

The bot instance is injected once at startup via `set_bot` (mirrors
services/oauth/server.py). Messages go to the tenant chat where the request
lives, mentioning the target; the new-request message carries the
accept/decline buttons (also reachable from the `reqs` hub inbox).
"""

from __future__ import annotations

from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from utils.logger import get_logger
from utils.formatting.text import escape_html

logger = get_logger("services.requests.notify")

_bot: Optional[Bot] = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def _mention(tg_id: int, name: str) -> str:
    return f'<a href="tg://user?id={tg_id}">{escape_html(name)}</a>'


async def notify_new_request(*, chat_id: int, request_id: int, sender_name: str,
                             target_tg_id: int, target_name: str, map_label: str,
                             conditions_text: str, note: Optional[str], lang: str) -> None:
    """Announce a new pending request to its target, with accept/decline buttons."""
    if _bot is None:
        return
    text = t(
        "req.notify.new", lang,
        target=_mention(target_tg_id, target_name),
        sender=escape_html(sender_name),
        map=escape_html(map_label),
        conditions=escape_html(conditions_text),
    )
    if note:
        text += t("req.notify.note", lang, note=escape_html(note))
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("req.kb.accept", lang), callback_data=f"rq:acc:{request_id}"),
        InlineKeyboardButton(text=t("req.kb.decline", lang), callback_data=f"rq:dec:{request_id}"),
    ]])
    try:
        await _bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML",
                                disable_web_page_preview=True)
    except Exception as exc:
        logger.debug(f"notify_new_request failed (chat={chat_id}): {exc}")


async def notify_completed(*, chat_id: int, sender_name: str, target_tg_id: int,
                           target_name: str, map_label: str, lang: str) -> None:
    """Announce that a target completed a request."""
    if _bot is None:
        return
    text = t(
        "req.notify.completed", lang,
        target=_mention(target_tg_id, target_name),
        sender=escape_html(sender_name),
        map=escape_html(map_label),
    )
    try:
        await _bot.send_message(chat_id, text, parse_mode="HTML",
                                disable_web_page_preview=True)
    except Exception as exc:
        logger.debug(f"notify_completed failed (chat={chat_id}): {exc}")
