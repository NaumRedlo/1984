"""Auto score-card: when an osu! score link is pasted in chat, fetch that
score for ANY player (app-level token, same pattern as maplink's beatmap
lookups) and render it as a recent-score-style card ("shared" header, since
a linked score isn't necessarily that player's most recent play).

Structural mirror of bot/handlers/maplink/handlers.py's on_beatmap_link:
reacts to any message carrying a score link, resolves+renders, fails silent
(this is a passive background reaction, not a command the user typed).
"""

from __future__ import annotations

from aiogram import Router, types, F
from aiogram.types import (
    BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
)

from services.image import card_renderer
from services.image.render.recent import build_recent_card_data
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu.helpers import remember_message_context
from utils.osu.score_link import extract_score_ref, LINK_HINT_RE

logger = get_logger("handlers.scorelink")

router = Router(name="scorelink")

_LINK_FILTER = F.text.func(lambda t: bool(t) and bool(LINK_HINT_RE.search(t)))


async def _resolve_score_owner(osu_api_client, raw_score: dict) -> tuple[str, int, str]:
    """(username, user_id, cover_url). Prefers the score's own embedded
    `user` (no extra call); falls back to get_user_data(user_id) when the
    embed is missing a cover — osu!'s embedded UserCompact isn't guaranteed
    to carry one, so this fallback is load-bearing, not decorative."""
    embedded = raw_score.get("user") or {}
    user_id = raw_score.get("user_id") or embedded.get("id")
    username = embedded.get("username")
    cover_url = (embedded.get("cover") or {}).get("url")
    if username and cover_url:
        return username, user_id, cover_url
    if not user_id:
        return username or "???", 0, cover_url or ""
    try:
        data = await osu_api_client.get_user_data(user_id)
    except Exception:
        data = None
    if data:
        return data.get("username") or username or "???", user_id, data.get("cover_url") or cover_url or ""
    return username or "???", user_id, cover_url or ""


@router.message(_LINK_FILTER, F.chat.type.in_({"private", "group", "supergroup"}))
async def on_score_link(message: types.Message, osu_api_client):
    text = message.text or ""
    if text.lstrip().startswith("/"):
        return  # a command carrying a link — let its own handler own it
    ref = extract_score_ref(text)
    if not ref:
        return
    if not osu_api_client:
        return

    try:
        raw_score = await osu_api_client.get_score(ref.score_id, mode=ref.mode)
    except Exception:
        logger.warning("scorelink: fetch failed", exc_info=True)
        return
    if not raw_score:
        return  # unknown/private score — stay silent, mirrors maplink

    try:
        username, player_id, cover_url = await _resolve_score_owner(osu_api_client, raw_score)
        lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
        requester_name = ""
        if message.from_user:
            requester_name = message.from_user.first_name or message.from_user.username or "???"
        data = await build_recent_card_data(
            raw_score, username=username, player_id=player_id, player_cover_url=cover_url,
            requester_name=requester_name, lang=lang, card_mode="shared",
        )
        png = (await card_renderer.generate_recent_card_async(data)).getvalue()
    except Exception:
        logger.warning("scorelink: build/render failed", exc_info=True)
        return

    mode = ref.mode or "osu"
    beatmap_url = f"https://osu.ppy.sh/beatmapsets/{data['beatmapset_id']}#{mode}/{data['beatmap_id']}"
    rows = [[
        InlineKeyboardButton(text=t("common.kb.beatmap", lang), url=beatmap_url),
        InlineKeyboardButton(text=t("common.kb.leaderboard", lang), callback_data=f"lbm:{data['beatmap_id']}"),
    ]]
    # Same rationale as rs's own render button: only offer it when osu! says
    # a replay actually exists for this score — we have no OAuth token for
    # an arbitrary score's owner, so an unconditional button would just fail
    # for every private replay.
    if raw_score.get("replay"):
        rows.append([InlineKeyboardButton(text=t("common.kb.render", lang), callback_data=f"rndr:{ref.score_id}")])

    try:
        sent = await message.answer_photo(
            BufferedInputFile(png, filename="score.png"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        remember_message_context(sent.chat.id, sent.message_id, data)
    except Exception:
        logger.warning("scorelink: send_photo failed", exc_info=True)


__all__ = ["router"]
