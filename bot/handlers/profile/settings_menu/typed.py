"""Settings you type rather than pick from a row of buttons.

A row of five percentages is a row of five slivers on a phone, it cannot say
63, and it grows a button every time somebody wants a value it does not have.
So the numbers are typed: one row per setting saying what it is now, and a tap
asks for the new one.

The asking is the careful part. This is a bot that lives in group chats, and a
handler that reads "the next message from whoever tapped" would eat somebody's
conversation the moment they tapped and then said something else. So it is
gated the way `WhatifReplyFilter` is gated — on a real reply to a real prompt
this module sent, from the person it was sent to — and anything else falls
through untouched. A prompt nobody answers is a message, not a state.
"""

from __future__ import annotations

import re
from typing import Callable, NamedTuple

from aiogram import F, Router, types
from aiogram.filters import BaseFilter
from aiogram.types import ForceReply, Message

from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _load, _store
from utils.i18n import t
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="settings_typed")


class Field(NamedTuple):
    """One typed setting: what it is called, and what it will accept."""

    key: str
    label: str
    hint: str
    parse: Callable[[str], object | None]


def _whole(low: int, high: int) -> Callable[[str], int | None]:
    """A whole number in range, from whatever somebody typed.

    `%`, spaces and a stray `fps` are dropped rather than refused: a person
    answering "how loud" with "80%" has answered it, and a bot that says no to
    that is being difficult about punctuation.
    """

    def parse(text: str) -> int | None:
        cleaned = re.sub(r"[^0-9-]", "", text or "")
        if not cleaned or not cleaned.lstrip("-").isdigit():
            return None
        value = int(cleaned)
        return value if low <= value <= high else None

    return parse


def _size(text: str) -> str | None:
    """`1600x900`, however it was written — `×`, spaces, or a capital X.

    Both sides must be even. Every encoder this feeds wants an even frame, and
    finding that out at the end of a render is finding it out too late.
    """
    match = re.match(r"^\s*(\d{3,5})\s*[x×X*]\s*(\d{3,5})\s*$", text or "")
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width % 2 or height % 2:
        return None
    if not (256 <= width <= 3840 and 256 <= height <= 2160):
        return None
    return f"{width}x{height}"


# Every setting somebody types, by the name it has on `Choices`.
FIELDS: dict[str, Field] = {
    "size": Field("size", "sts.qly.size", "sts.typed.size_hint", _size),
    "fps": Field("fps", "sts.qly.fps", "sts.typed.fps_hint", _whole(15, 240)),
    "dim": Field("dim", "sts.qly.dim", "sts.typed.percent_hint", _whole(0, 100)),
    "meter": Field("meter", "sts.qly.meter", "sts.typed.meter_hint", _whole(25, 300)),
    "cursor": Field("cursor", "sts.qly.cursor", "sts.typed.cursor_hint", _whole(40, 200)),
    "music": Field("music", "sts.snd.music", "sts.typed.percent_hint", _whole(0, 100)),
    "hitsounds": Field(
        "hitsounds", "sts.snd.hitsounds", "sts.typed.percent_hint", _whole(0, 100)
    ),
    "volume": Field("volume", "sts.snd.volume", "sts.typed.volume_hint", _whole(0, 200)),
}

# Which prompt is waiting on which answer: (chat, prompt message) -> (who, what).
#
# Held in memory rather than in the row. It is a question in flight, not a
# preference, and a bot that restarts mid-question should forget it rather than
# come back still waiting — the prompt it was waiting on is scrolled away by
# then anyway.
_ASKED: dict[tuple[int, int], tuple[int, str]] = {}

# Enough that a person can go and look something up, not so many that a chat
# fills with them.
_MOST = 64


def value_button(choices: renders.Choices, key: str, lang: str):
    """The one row a typed setting gets: its name, and what it is now."""
    from aiogram.types import InlineKeyboardButton

    field = FIELDS[key]
    now = getattr(choices, key, None)
    shown = t("sts.typed.as_it_comes", lang) if now is None else _shown(key, now)
    return InlineKeyboardButton(
        text=f"{t(field.label, lang)} — {shown}",
        callback_data=f"st:typed:{key}",
    )


def _shown(key: str, value) -> str:
    if key == "size":
        return str(value).replace("x", "×")
    if key == "fps":
        return f"{value} fps"
    return f"{value}%"


@router.callback_query(F.data.startswith("st:typed:"))
async def cb_ask(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    key = callback.data.split(":", 2)[2]
    field = FIELDS.get(key)
    if field is None:
        await callback.answer(t("sts.rnd.unknown", lang), show_alert=True)
        return
    prompt = await callback.message.answer(
        t("sts.typed.ask", lang, name=t(field.label, lang), hint=t(field.hint, lang)),
        reply_markup=ForceReply(selective=True),
    )
    if len(_ASKED) > _MOST:
        # Oldest first. A question nobody answered is not worth a slot once the
        # next sixty-four have been asked.
        _ASKED.pop(next(iter(_ASKED)))
    _ASKED[(prompt.chat.id, prompt.message_id)] = (callback.from_user.id, key)
    await callback.answer()


class AnswerFilter(BaseFilter):
    """A reply to a prompt this module sent, from the person it was sent to.

    Everything else is somebody talking. Both halves matter: without the reply
    the handler would read the chat, and without the person a passer-by could
    answer somebody else's settings.
    """

    async def __call__(self, message: Message) -> bool | dict:
        reply = message.reply_to_message
        if reply is None or not (message.text or "").strip():
            return False
        waiting = _ASKED.get((reply.chat.id, reply.message_id))
        if waiting is None:
            return False
        who, key = waiting
        if message.from_user is None or message.from_user.id != who:
            return False
        return {"typed_key": key}


@router.message(AnswerFilter())
async def on_answer(
    message: types.Message, typed_key: str, tenant_chat_id=None, lang: str = "en"
) -> None:
    field = FIELDS[typed_key]
    value = field.parse(message.text)
    if value is None:
        await message.reply(t("sts.typed.no", lang, hint=t(field.hint, lang)))
        return

    choices = await _load(message.from_user.id, tenant_chat_id)
    # The same guard the buttons pass through. 4K can be reached by hand as
    # easily as by button, and a rule only one of the two ways obeyed would be a
    # rule with a way round it — see `render.rationed`.
    from dataclasses import replace

    from bot.handlers.profile.settings_menu.render import rationed

    wanted = replace(choices, **{typed_key: value})
    refusal = await rationed(message.from_user.id, tenant_chat_id, choices, wanted, lang)
    if refusal:
        await message.reply(refusal)
        return

    _ASKED.pop((message.reply_to_message.chat.id, message.reply_to_message.message_id), None)
    setattr(choices, typed_key, value)
    await _store(message.from_user.id, tenant_chat_id, choices)
    # The prompt and the answer both go: the screen they belong to is still up
    # the chat and says the new figure itself, and two messages saying "80" is
    # two messages nobody needs.
    for doomed in (message.reply_to_message, message):
        try:
            await doomed.delete()
        except Exception:  # noqa: BLE001 — no rights, or already gone
            pass
    await message.answer(
        t("sts.typed.set", lang, name=t(field.label, lang), value=_shown(typed_key, value))
    )


__all__ = ["router", "FIELDS", "value_button", "AnswerFilter"]
