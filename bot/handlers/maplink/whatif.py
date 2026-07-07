"""`map` command — a hypothetical performance calculator.

"map <link> <accuracy> [mods]" (self-contained) or, replying to any message
that carries a beatmap link (a raw link, or a card this bot already rendered
for one), "map <accuracy> [mods]". Shows a NEW dedicated card: PP/star-rating
at that accuracy+mods, and the 300/100/50 breakdown rosu-pp itself picked to
reach it — not a real play.

Unlike the passive on_beatmap_link auto-detect in handlers.py, failures here
are shown to the user: this is an explicit command they typed, not a silent
background reaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram import Router, types
from aiogram.types import (
    BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
)

from bot.filters import TextTriggerFilter, TriggerArgs
from services.image import card_renderer
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.osu.beatmap_link import BeatmapRef, extract_beatmap_ref
from utils.osu.helpers import get_message_context
from utils.osu.mod_utils import KNOWN_PP_MODS, apply_mods, parse_mods_tokens
from utils.osu.pp_calculator import calculate_whatif_pp
from utils.osu.resolve_user import get_real_reply

from bot.handlers.maplink.handlers import _resolve_card

logger = get_logger("handlers.maplink.whatif")

router = Router(name="maplink_whatif")


@dataclass
class _WhatifArgs:
    beatmap_ref: BeatmapRef
    accuracy: float
    mods_str: str


def _usage_html() -> str:
    return (
        "Использование: <code>map &lt;ссылка&gt; &lt;точность&gt; [моды]</code>\n"
        "или ответом на сообщение со ссылкой: <code>map &lt;точность&gt; [моды]</code>\n"
        "Например: <code>map https://osu.ppy.sh/beatmaps/129891 94 hr</code>"
    )


def _parse_whatif_args(trigger_args: Optional[TriggerArgs], message: types.Message):
    """Returns (_WhatifArgs, None) on success, or (None, error_html)."""
    tokens = (trigger_args.args or "").split() if trigger_args else []

    ref = None
    rest = tokens
    for i, tok in enumerate(tokens):
        r = extract_beatmap_ref(tok)
        if r:
            ref = r
            rest = tokens[:i] + tokens[i + 1:]
            break

    if ref is None:
        reply = get_real_reply(message)
        if reply is not None:
            ref = extract_beatmap_ref(reply.text or reply.caption)
            if ref is None:
                ctx = get_message_context(reply.chat.id, reply.message_id)
                if ctx and ctx.get("beatmap_id"):
                    ref = BeatmapRef(int(ctx["beatmap_id"]), ctx.get("beatmapset_id"), None)
            if ref is not None:
                rest = tokens

    if ref is None:
        return None, _usage_html()

    if not rest:
        return None, "Укажи точность, например: <code>map 94 hr</code>"

    acc_raw = rest[0].rstrip("%").replace(",", ".")
    try:
        accuracy = float(acc_raw)
    except ValueError:
        return None, f"Некорректная точность: <code>{escape_html(rest[0])}</code>"
    if not (0.0 < accuracy <= 100.0):
        return None, "Точность должна быть в диапазоне 0–100%."

    mods_str = "".join(rest[1:]).upper()
    if mods_str:
        mod_tokens = parse_mods_tokens(mods_str)
        bad = [t for t in mod_tokens if t not in KNOWN_PP_MODS]
        if bad or "".join(mod_tokens) != mods_str:
            return None, f"Неизвестный мод: <code>{escape_html(mods_str)}</code>"

    return _WhatifArgs(ref, accuracy, mods_str), None


@router.message(TextTriggerFilter("map"))
async def cmd_whatif(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    if not osu_api_client:
        await message.answer("Ошибка: API-клиент не инициализирован.")
        return

    parsed, err = _parse_whatif_args(trigger_args, message)
    if err:
        await message.answer(err, parse_mode="HTML")
        return

    try:
        card_data = await _resolve_card(parsed.beatmap_ref, osu_api_client)
    except Exception:
        logger.warning("whatif: resolve failed", exc_info=True)
        card_data = None
    if not card_data:
        await message.answer("Карта не найдена.")
        return

    whatif = await calculate_whatif_pp(card_data["beatmap_id"], parsed.accuracy, parsed.mods_str)
    if not whatif:
        await message.answer("Не удалось рассчитать pp для этой карты.")
        return

    adjusted = apply_mods(
        float(card_data.get("cs") or 0), float(card_data.get("ar") or 0),
        float(card_data.get("od") or 0), float(card_data.get("hp_drain") or 0),
        float(card_data.get("bpm") or 0), int(card_data.get("length") or 0),
        parsed.mods_str,
    )

    data = {
        "beatmap_id": card_data["beatmap_id"], "beatmapset_id": card_data["beatmapset_id"],
        "artist": card_data["artist"], "title": card_data["title"],
        "version": card_data["version"], "creator": card_data["creator"],
        "status": card_data["status"], "cover_url": card_data["cover_url"], "url": card_data["url"],
        "star_rating": whatif["star_rating"],
        "accuracy": parsed.accuracy,
        "mods": parsed.mods_str,
        "pp": whatif["pp"],
        "max_combo": whatif["combo"],
        "count_300": whatif["count_300"], "count_100": whatif["count_100"],
        "count_50": whatif["count_50"], "count_miss": whatif["count_miss"],
        "cs": adjusted["cs"], "ar": adjusted["ar"], "od": adjusted["od"], "hp_drain": adjusted["hp"],
        "bpm": adjusted["bpm"], "length": adjusted["total_length"],
        "brackets": whatif["brackets"],
    }

    try:
        png = (await card_renderer.generate_whatif_card_async(data)).getvalue()
    except Exception:
        logger.warning("whatif: render failed", exc_info=True)
        await message.answer("Не удалось отрисовать карточку.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 osu!", url=data["url"]),
    ]])
    try:
        await message.answer_photo(
            BufferedInputFile(png, filename="whatif.png"), reply_markup=kb,
        )
    except Exception:
        logger.warning("whatif: send_photo failed", exc_info=True)


__all__ = ["router"]
