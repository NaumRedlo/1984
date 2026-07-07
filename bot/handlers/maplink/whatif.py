"""`map` command — a hypothetical performance calculator.

Replying to a beatmap card the bot has already shown (either the interactive
what-if card on_beatmap_link now posts automatically for every pasted link,
or a previous `map` result), "map <accuracy> [mods]" jumps straight to that
accuracy — a text shortcut on top of the same interactive keyboard's ±steps.

The beatmap itself is NEVER parsed out of this command's own text — only
from context the bot already recorded when it noticed the link (see
remember_message_context calls in handlers.py and this module). There is
no self-contained "map <link> <accuracy>" form; the bot finding the link is
what "notices" it, not the command.

Unlike the passive on_beatmap_link auto-detect in handlers.py, failures here
are shown to the user: this is an explicit command they typed, not a silent
background reaction.

The result carries an interactive keyboard (mod toggles + ±0.1/±0.5/±1
accuracy steps) that edits the same message in place — see _whatif_keyboard
and whatif_callback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram import F, Router, types
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto,
)

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.utils.safe_edit import safe_edit_media
from services.image import card_renderer
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.osu.beatmap_link import BeatmapRef
from utils.osu.helpers import get_message_context
from utils.osu.mod_utils import KNOWN_PP_MODS, WHATIF_MOD_SET, apply_mods, parse_mods_tokens
from utils.osu.pp_calculator import calculate_whatif_pp
from utils.osu.resolve_user import get_real_reply

from bot.handlers.maplink.resolve import _resolve_card

logger = get_logger("handlers.maplink.whatif")

router = Router(name="maplink_whatif")

# Accuracy-step callback actions, in tenths of a percent (so all callback-data
# arithmetic stays integer — no float drift across repeated button presses).
_ACC_STEPS = (("-1", -10), ("-0.5", -5), ("-0.1", -1), ("+0.1", 1), ("+0.5", 5), ("+1", 10))


@dataclass
class _WhatifArgs:
    beatmap_ref: BeatmapRef
    accuracy: float
    mods_str: str


def _usage_html() -> str:
    return (
        "Ответь этой командой на карточку карты: <code>map &lt;точность&gt; [моды]</code>\n"
        "Например: <code>map 94 hr</code>\n"
        "(Карточка карты появляется автоматически, когда в чат кидают ссылку на неё.)"
    )


def _parse_whatif_args(trigger_args: Optional[TriggerArgs], message: types.Message):
    """Returns (_WhatifArgs, None) on success, or (None, error_html).

    The beatmap comes ONLY from context the bot itself already recorded for
    the replied-to message (remember_message_context) — never parsed out of
    this command's own args or the reply's raw text/caption."""
    tokens = (trigger_args.args or "").split() if trigger_args else []

    ref = None
    reply = get_real_reply(message)
    if reply is not None:
        ctx = get_message_context(reply.chat.id, reply.message_id)
        if ctx and ctx.get("beatmap_id"):
            ref = BeatmapRef(int(ctx["beatmap_id"]), ctx.get("beatmapset_id"), None)

    if ref is None:
        return None, _usage_html()

    if not tokens:
        return None, "Укажи точность, например: <code>map 94 hr</code>"

    acc_raw = tokens[0].rstrip("%").replace(",", ".")
    try:
        accuracy = float(acc_raw)
    except ValueError:
        return None, f"Некорректная точность: <code>{escape_html(tokens[0])}</code>"
    if not (0.0 < accuracy <= 100.0):
        return None, "Точность должна быть в диапазоне 0–100%."

    mods_str = "".join(tokens[1:]).upper()
    if mods_str:
        mod_tokens = parse_mods_tokens(mods_str)
        bad = [t for t in mod_tokens if t not in KNOWN_PP_MODS]
        if bad or "".join(mod_tokens) != mods_str:
            return None, f"Неизвестный мод: <code>{escape_html(mods_str)}</code>"

    return _WhatifArgs(ref, accuracy, mods_str), None


async def _build_whatif_data(ref: BeatmapRef, accuracy: float, mods_str: str, osu_api_client) -> Optional[dict]:
    """Resolve `ref` + calculate PP/mod-adjusted stats at `accuracy`+`mods_str`
    into the dict `generate_whatif_card` expects. Shared by the initial `map`
    command and the interactive keyboard's callback re-renders — a callback
    always passes a `BeatmapRef(beatmap_id, None, None)` since by then the
    exact beatmap is already known."""
    try:
        card_data = await _resolve_card(ref, osu_api_client)
    except Exception:
        logger.warning("whatif: resolve failed", exc_info=True)
        return None
    if not card_data:
        return None

    whatif = await calculate_whatif_pp(card_data["beatmap_id"], accuracy, mods_str)
    if not whatif:
        return None

    adjusted = apply_mods(
        float(card_data.get("cs") or 0), float(card_data.get("ar") or 0),
        float(card_data.get("od") or 0), float(card_data.get("hp_drain") or 0),
        float(card_data.get("bpm") or 0), int(card_data.get("length") or 0),
        mods_str,
    )

    return {
        "beatmap_id": card_data["beatmap_id"], "beatmapset_id": card_data["beatmapset_id"],
        "artist": card_data["artist"], "title": card_data["title"],
        "version": card_data["version"], "creator": card_data["creator"],
        "status": card_data["status"], "cover_url": card_data["cover_url"], "url": card_data["url"],
        "star_rating": whatif["star_rating"],
        "accuracy": accuracy,
        "mods": mods_str,
        "pp": whatif["pp"],
        "max_combo": whatif["combo"],
        "count_300": whatif["count_300"], "count_100": whatif["count_100"],
        "count_50": whatif["count_50"], "count_miss": whatif["count_miss"],
        "cs": adjusted["cs"], "ar": adjusted["ar"], "od": adjusted["od"], "hp_drain": adjusted["hp"],
        "bpm": adjusted["bpm"], "length": adjusted["total_length"],
        "brackets": whatif["brackets"],
    }


def _whatif_keyboard(beatmap_id: int, accuracy: float, mods_str: str, url: str) -> InlineKeyboardMarkup:
    """Mod toggles (row 1) + ±0.1/±0.5/±1 accuracy steps around a read-only
    current-value button (row 2) + the osu! link (row 3). All state needed to
    recompute lives in the callback_data itself — no per-user session."""
    acc_x10 = round(accuracy * 10)

    def cb(action: str) -> str:
        return f"wif:{beatmap_id}:{acc_x10}:{mods_str}:{action}"

    active = set(parse_mods_tokens(mods_str))
    mod_row = [
        InlineKeyboardButton(text=f"• {m} •" if m in active else m, callback_data=cb(f"m{m}"))
        for m in WHATIF_MOD_SET
    ]

    acc_row = [InlineKeyboardButton(text=label, callback_data=cb(f"a{delta:+d}")) for label, delta in _ACC_STEPS[:3]]
    acc_row.append(InlineKeyboardButton(text=f"{accuracy:.1f}%", callback_data=cb("noop")))
    acc_row += [InlineKeyboardButton(text=label, callback_data=cb(f"a{delta:+d}")) for label, delta in _ACC_STEPS[3:]]

    return InlineKeyboardMarkup(inline_keyboard=[
        mod_row,
        acc_row,
        [InlineKeyboardButton(text="🔗 osu!", url=url)],
    ])


@router.message(TextTriggerFilter("map"))
async def cmd_whatif(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    if not osu_api_client:
        await message.answer("Ошибка: API-клиент не инициализирован.")
        return

    parsed, err = _parse_whatif_args(trigger_args, message)
    if err:
        await message.answer(err, parse_mode="HTML")
        return

    data = await _build_whatif_data(parsed.beatmap_ref, parsed.accuracy, parsed.mods_str, osu_api_client)
    if not data:
        await message.answer("Карта не найдена или не удалось рассчитать pp.")
        return

    try:
        png = (await card_renderer.generate_whatif_card_async(data)).getvalue()
    except Exception:
        logger.warning("whatif: render failed", exc_info=True)
        await message.answer("Не удалось отрисовать карточку.")
        return

    kb = _whatif_keyboard(data["beatmap_id"], data["accuracy"], data["mods"], data["url"])
    try:
        await message.answer_photo(
            BufferedInputFile(png, filename="whatif.png"), reply_markup=kb,
        )
    except Exception:
        logger.warning("whatif: send_photo failed", exc_info=True)


@router.callback_query(F.data.startswith("wif:"))
async def whatif_callback(callback: CallbackQuery, osu_api_client=None):
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    _, beatmap_id_str, acc_x10_str, mods_str, action = parts

    if action == "noop":
        await callback.answer()
        return
    if not beatmap_id_str.isdigit() or not acc_x10_str.isdigit():
        await callback.answer()
        return
    beatmap_id = int(beatmap_id_str)
    acc_x10 = int(acc_x10_str)

    if action[0] == "a" and action[1:].lstrip("+-").isdigit():
        acc_x10 = max(1, min(1000, acc_x10 + int(action[1:])))
    elif action[0] == "m" and action[1:] in WHATIF_MOD_SET:
        mod = action[1:]
        active = set(parse_mods_tokens(mods_str))
        active.symmetric_difference_update({mod})
        mods_str = "".join(m for m in WHATIF_MOD_SET if m in active)
    else:
        await callback.answer()
        return

    if not osu_api_client:
        await callback.answer("Ошибка: API-клиент не инициализирован.", show_alert=True)
        return

    accuracy = acc_x10 / 10.0
    data = await _build_whatif_data(BeatmapRef(beatmap_id, None, None), accuracy, mods_str, osu_api_client)
    if not data:
        await callback.answer("Не удалось пересчитать.", show_alert=True)
        return

    try:
        png = (await card_renderer.generate_whatif_card_async(data)).getvalue()
    except Exception:
        logger.warning("whatif callback: render failed", exc_info=True)
        await callback.answer("Не удалось отрисовать карточку.", show_alert=True)
        return

    kb = _whatif_keyboard(beatmap_id, accuracy, mods_str, data["url"])
    await safe_edit_media(
        callback.message,
        media=InputMediaPhoto(media=BufferedInputFile(png, filename="whatif.png")),
        reply_markup=kb,
    )
    await callback.answer()


__all__ = ["router"]
