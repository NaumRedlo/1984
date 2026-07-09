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
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, Message,
)

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.utils.safe_edit import is_benign_edit_race, safe_edit_media
from services.image import card_renderer
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu.beatmap_link import BeatmapRef
from utils.osu.helpers import get_message_context, remember_message_context
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


def _reply_beatmap_ref(message: types.Message) -> Optional[BeatmapRef]:
    """The beatmap the replied-to message is a card for — resolved ONLY from
    context the bot itself recorded (remember_message_context) for that EXACT
    message, never parsed out of raw text and never guessed from some other
    card posted earlier in the chat (strict=True — get_message_context's
    "latest beatmap in this chat" fallback is only safe for callers gated
    behind an explicit command; this is used by a bare-text reply trigger, so
    a loose match would misfire on ordinary conversation replies). None if
    the message isn't a reply to such a card."""
    reply = get_real_reply(message)
    if reply is None:
        return None
    ctx = get_message_context(reply.chat.id, reply.message_id, strict=True)
    if ctx and ctx.get("beatmap_id"):
        return BeatmapRef(int(ctx["beatmap_id"]), ctx.get("beatmapset_id"), None)
    return None


def _looks_like_accuracy(token: str) -> bool:
    try:
        acc = float(token.rstrip("%").replace(",", "."))
    except ValueError:
        return False
    return 0.0 < acc <= 100.0


class WhatifReplyFilter(BaseFilter):
    """Bare "<accuracy> [mods]" (no "map" keyword) when it's a reply to a
    beatmap card the bot posted — e.g. replying "80 ez" to the auto-detected
    card. Gated on a real reply-to-card + a numeric first token so it can't
    swallow ordinary chat. Injects `whatif_text` (the accuracy+mods part)."""

    async def __call__(self, message: Message) -> bool | dict:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        tokens = text.split()
        if not _looks_like_accuracy(tokens[0]):
            return False
        if _reply_beatmap_ref(message) is None:
            return False
        return {"whatif_text": text}


def _parse_whatif_args(text: str, message: types.Message, lang: str = "en"):
    """Returns (_WhatifArgs, None) on success, or (None, error_html). `text`
    is the accuracy+mods part ("80 ez"), with any leading "map" already
    stripped by the caller. Error text is localised to `lang`."""
    tokens = (text or "").split()

    ref = _reply_beatmap_ref(message)
    if ref is None:
        return None, t("wif.usage", lang)

    if not tokens:
        return None, t("wif.need_accuracy", lang)

    acc_raw = tokens[0].rstrip("%").replace(",", ".")
    try:
        accuracy = float(acc_raw)
    except ValueError:
        return None, t("wif.bad_accuracy", lang, value=escape_html(tokens[0]))
    if not (0.0 < accuracy <= 100.0):
        return None, t("wif.accuracy_range", lang)

    mods_str = "".join(tokens[1:]).upper()
    if mods_str:
        mod_tokens = parse_mods_tokens(mods_str)
        bad = [mt for mt in mod_tokens if mt not in KNOWN_PP_MODS]
        if bad or "".join(mod_tokens) != mods_str:
            return None, t("wif.unknown_mod", lang, mods=escape_html(mods_str))

    return _WhatifArgs(ref, accuracy, mods_str), None


async def _build_whatif_data(ref: BeatmapRef, accuracy: float, mods_str: str, osu_api_client,
                             lang: str = "en") -> Optional[dict]:
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
        "mapper_id": card_data.get("mapper_id"),
        "status": card_data["status"], "cover_url": card_data["cover_url"], "url": card_data["url"],
        "lang": lang,
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


# Which control sections are expanded, as a bitmask carried in callback_data
# (bit 0 = mods open, bit 1 = accuracy open) — collapsed by default so the
# card starts uncluttered and the user reveals each section on demand.
_VIEW_MODS = 1
_VIEW_ACC = 2


def _whatif_keyboard(beatmap_id: int, accuracy: float, mods_str: str, url: str,
                     view: int = 0, lang: str = "en") -> InlineKeyboardMarkup:
    """An accordion keyboard: a "Mods" header that reveals its 5 mod toggles
    when pressed, an "Accuracy" header that reveals its ±0.1/±0.5/±1 steppers
    around a read-only current-value button, then the leaderboard + osu! link.
    `view` (a bitmask) says which sections are currently expanded — all state
    needed to recompute lives in the callback_data itself, no per-user
    session. Labels follow the viewer's `lang`."""
    acc_x10 = round(accuracy * 10)

    def cb(action: str) -> str:
        return f"wif:{beatmap_id}:{acc_x10}:{mods_str}:{view}:{action}"

    mods_open = bool(view & _VIEW_MODS)
    acc_open = bool(view & _VIEW_ACC)
    rows = [[InlineKeyboardButton(text=f"{t('wif.kb.mods', lang)} {'▾' if mods_open else '▸'}", callback_data=cb("vm"))]]
    if mods_open:
        active = set(parse_mods_tokens(mods_str))
        rows.append([
            InlineKeyboardButton(text=f"• {m} •" if m in active else m, callback_data=cb(f"m{m}"))
            for m in WHATIF_MOD_SET
        ])
    rows.append([InlineKeyboardButton(text=f"{t('wif.kb.acc', lang)} {'▾' if acc_open else '▸'}", callback_data=cb("va"))])
    if acc_open:
        acc_row = [InlineKeyboardButton(text=label, callback_data=cb(f"a{delta:+d}")) for label, delta in _ACC_STEPS[:3]]
        acc_row.append(InlineKeyboardButton(text=f"{accuracy:.1f}%", callback_data=cb("noop")))
        acc_row += [InlineKeyboardButton(text=label, callback_data=cb(f"a{delta:+d}")) for label, delta in _ACC_STEPS[3:]]
        rows.append(acc_row)
    # Leaderboard reuses the existing lbm callback — the local leaderboard for
    # this beatmap among registered players (see leaderboard/handlers.py).
    rows.append([
        InlineKeyboardButton(text=t("common.kb.leaderboard", lang), callback_data=f"lbm:{beatmap_id}"),
        InlineKeyboardButton(text="🔗 osu!", url=url),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(TextTriggerFilter("map"))
async def cmd_whatif_keyword(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """Legacy "map <accuracy> [mods]" keyword form — still supported, and the
    only form that shows a usage hint when used without a card reply."""
    await _handle_whatif(message, trigger_args.args or "", osu_api_client)


@router.message(WhatifReplyFilter())
async def cmd_whatif_bare(message: types.Message, whatif_text: str, osu_api_client):
    """Bare "<accuracy> [mods]" reply form (no "map" keyword)."""
    await _handle_whatif(message, whatif_text, osu_api_client)


async def _handle_whatif(message: types.Message, text: str, osu_api_client) -> None:
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"

    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
        return

    parsed, err = _parse_whatif_args(text, message, lang)
    if err:
        await message.answer(err, parse_mode="HTML")
        return

    data = await _build_whatif_data(parsed.beatmap_ref, parsed.accuracy, parsed.mods_str,
                                    osu_api_client, lang)
    if not data:
        await message.answer(t("wif.map_not_found", lang))
        return

    try:
        png = (await card_renderer.generate_whatif_card_async(data)).getvalue()
    except Exception:
        logger.warning("whatif: render failed", exc_info=True)
        await message.answer(t("wif.render_failed", lang))
        return

    kb = _whatif_keyboard(data["beatmap_id"], data["accuracy"], data["mods"], data["url"], lang=lang)

    # Reply form edits the replied-to card in place rather than posting a new
    # one (same feel as the accordion buttons). The reply is always a bot card
    # here — the beatmap was resolved from its recorded context — but fall back
    # to a fresh card if it can't be edited (too old, deleted, etc.).
    reply = get_real_reply(message)
    if reply is not None:
        try:
            await safe_edit_media(
                reply,
                media=InputMediaPhoto(media=BufferedInputFile(png, filename="whatif.png")),
                reply_markup=kb,
            )
            return
        except Exception:
            logger.debug("whatif: reply edit failed, sending new card", exc_info=True)

    try:
        sent = await message.answer_photo(
            BufferedInputFile(png, filename="whatif.png"), reply_markup=kb,
        )
        remember_message_context(sent.chat.id, sent.message_id, {
            "beatmap_id": data["beatmap_id"], "beatmapset_id": data.get("beatmapset_id"),
        })
    except Exception:
        logger.warning("whatif: send_photo failed", exc_info=True)


@router.callback_query(F.data.startswith("wif:"))
async def whatif_callback(callback: CallbackQuery, osu_api_client=None):
    parts = (callback.data or "").split(":")
    if len(parts) != 6:
        await callback.answer()
        return
    _, beatmap_id_str, acc_x10_str, mods_str, view_str, action = parts

    if action == "noop":
        await callback.answer()
        return
    if not (beatmap_id_str.isdigit() and acc_x10_str.isdigit() and view_str.isdigit()):
        await callback.answer()
        return
    beatmap_id = int(beatmap_id_str)
    acc_x10 = int(acc_x10_str)
    view = int(view_str)

    # Card + keyboard follow the language of whoever pressed the button.
    lang = (await get_language(callback.from_user.id)).lower() if callback.from_user else "en"

    # Section expand/collapse — only touches the keyboard, so edit the markup
    # in place without re-rendering (and re-uploading) the card image.
    if action in ("vm", "va"):
        view ^= _VIEW_MODS if action == "vm" else _VIEW_ACC
        kb = _whatif_keyboard(beatmap_id, acc_x10 / 10.0, mods_str,
                              f"https://osu.ppy.sh/b/{beatmap_id}", view, lang)
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except TelegramBadRequest as e:
            if not is_benign_edit_race(e):
                raise
        await callback.answer()
        return

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
        await callback.answer(t("common.api_not_ready", lang), show_alert=True)
        return

    accuracy = acc_x10 / 10.0
    data = await _build_whatif_data(BeatmapRef(beatmap_id, None, None), accuracy, mods_str,
                                    osu_api_client, lang)
    if not data:
        await callback.answer(t("wif.recalc_failed", lang), show_alert=True)
        return

    try:
        png = (await card_renderer.generate_whatif_card_async(data)).getvalue()
    except Exception:
        logger.warning("whatif callback: render failed", exc_info=True)
        await callback.answer(t("wif.render_failed", lang), show_alert=True)
        return

    kb = _whatif_keyboard(beatmap_id, accuracy, mods_str, data["url"], view, lang)
    await safe_edit_media(
        callback.message,
        media=InputMediaPhoto(media=BufferedInputFile(png, filename="whatif.png")),
        reply_markup=kb,
    )
    await callback.answer()


__all__ = ["router"]
