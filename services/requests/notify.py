"""Proactive request notifications.

The bot instance is injected once at startup via `set_bot` (mirrors
services/oauth/server.py). Messages go to the tenant chat where the request
lives, mentioning the target; the new-request message carries the
accept/decline buttons (also reachable from the `reqs` hub inbox).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile

from utils.i18n import t
from utils.logger import get_logger
from utils.formatting.text import escape_html
from services.requests.format import map_label as _map_label, map_link_html, stars_suffix

logger = get_logger("services.requests.notify")

_bot: Optional[Bot] = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def _mention(tg_id: int, name: str) -> str:
    return f'<a href="tg://user?id={tg_id}">{escape_html(name)}</a>'


def _accept_decline_kb(request_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("req.kb.accept", lang), callback_data=f"rq:acc:{request_id}"),
        InlineKeyboardButton(text=t("req.kb.decline", lang), callback_data=f"rq:dec:{request_id}"),
    ]])


async def notify_new_request(request_id: int) -> None:
    """Render the request as an image card and deliver it to the target (in their
    language), with accept/decline buttons. Falls back to a text card if the
    image render or photo send fails."""
    if _bot is None:
        return
    from db.database import get_db_session
    from db.models.map_request import MapRequest
    from db.models.user import User
    from utils.language import get_language
    from services.requests.conditions import parse, describe, condition_pills, parse_mods

    async with get_db_session() as s:
        req = await s.get(MapRequest, request_id)
        if not req:
            return
        sender = await s.get(User, req.sender_user_id)
        target = await s.get(User, req.target_user_id)
    if not (sender and target):
        return

    lang = (await get_language(target.telegram_id)).lower()
    cond = parse(req.conditions)
    conditions_text = describe(cond, t, lang)
    kb = _accept_decline_kb(req.id, lang)
    mention = _mention(target.telegram_id, target.osu_username)

    # Primary: an image card with a short mention caption (the ping).
    try:
        from services.image.render.request_card import render_request_card
        png = await asyncio.to_thread(render_request_card, {
            "lang": lang,
            "sender_name": sender.osu_username,
            "avatar_bytes": sender.avatar_data,
            "artist": req.artist, "title": req.title, "version": req.version,
            "star_rating": req.star_rating, "bpm": req.bpm, "length": req.length,
            "max_combo": req.map_max_combo,
            "condition_pills": condition_pills(cond, t, lang),
            "mods": list(parse_mods(cond.get("mods"))),
        })
        caption = t("req.notify.caption", lang, target=mention)
        await _bot.send_photo(
            req.tenant_chat_id, BufferedInputFile(png, filename="request.png"),
            caption=caption, reply_markup=kb, parse_mode="HTML",
        )
        return
    except Exception as exc:
        logger.warning(f"request card failed, falling back to text (req={req.id}): {exc}")

    # Fallback: the plain text card.
    label = _map_label(req.artist, req.title, req.version, req.beatmap_id)
    text = t(
        "req.notify.new", lang, target=mention, sender=escape_html(sender.osu_username),
        map=map_link_html(label, req.beatmap_id, req.beatmapset_id),
        stars=stars_suffix(req.star_rating), conditions=escape_html(conditions_text),
    )
    try:
        await _bot.send_message(req.tenant_chat_id, text, reply_markup=kb,
                                parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logger.debug(f"notify_new_request fallback failed (req={req.id}): {exc}")


async def notify_completed(*, chat_id: int, sender_name: str, target_tg_id: int,
                           target_name: str, map_label: str, beatmap_id: int,
                           beatmapset_id: Optional[int], lang: str) -> None:
    """Announce that a target completed a request."""
    if _bot is None:
        return
    text = t(
        "req.notify.completed", lang,
        target=_mention(target_tg_id, target_name),
        sender=escape_html(sender_name),
        map=map_link_html(map_label, beatmap_id, beatmapset_id),
    )
    try:
        await _bot.send_message(chat_id, text, parse_mode="HTML",
                                disable_web_page_preview=True)
    except Exception as exc:
        logger.debug(f"notify_completed failed (chat={chat_id}): {exc}")
