"""Render the last score via the inline 🎬 button on rs/recent cards.

Button-only (the rdr text command was removed): the button carries the score id
in its callback, and the card's stored context supplies the beatmapset, player
name, length, and the score snapshot for the render library.
"""

from aiogram import Router, F, types

from utils.i18n import t
from utils.language import get_language
from utils.osu.helpers import get_message_context
from bot.handlers.profile.render.library import _meta_from_ctx
from bot.handlers.profile.render.pipeline import render_gate, run_guarded_render

router = Router(name="render_score")


@router.callback_query(F.data.startswith("rndr:"))
async def cb_render_score(callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None):
    tg_id = callback.from_user.id
    lang = (await get_language(tg_id)).lower()

    gate = render_gate(tg_id)
    if gate == "busy":
        await callback.answer(t("render.busy", lang), show_alert=True)
        return
    if gate and gate.startswith("cooldown:"):
        await callback.answer(t("render.cooldown_short", lang, sec=gate.split(":")[1]), show_alert=True)
        return

    try:
        score_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    # The card's stored context carries the beatmapset + player name + length, plus
    # the score details we snapshot into the render library.
    ctx = get_message_context(callback.message.chat.id, callback.message.message_id) or {}
    beatmapset_id = ctx.get("beatmapset_id")
    display_name = ctx.get("username", "")
    length_seconds = ctx.get("total_length")
    meta = _meta_from_ctx(ctx)

    await callback.answer(t("render.started", lang))
    await run_guarded_render(
        callback.message, score_id=score_id, beatmapset_id=beatmapset_id,
        display_name=display_name, length_seconds=length_seconds, meta=meta,
        tg_id=tg_id, tenant_chat_id=tenant_chat_id, osu_api_client=osu_api_client, lang=lang,
    )
