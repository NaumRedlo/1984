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

    key: str
    label: str
    hint: str
    parse: Callable[[str], object | None]
    low: int | None = None
    high: int | None = None

def _whole(low: int, high: int) -> Callable[[str], int | None]:

    def parse(text: str) -> int | None:
        cleaned = re.sub(r"[^0-9-]", "", text or "")
        if not cleaned or not cleaned.lstrip("-").isdigit():
            return None
        value = int(cleaned)
        return value if low <= value <= high else None

    return parse

def _size(text: str) -> str | None:
    match = re.match(r"^\s*(\d{3,5})\s*[x×X*]\s*(\d{3,5})\s*$", text or "")
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width % 2 or height % 2:
        return None
    if not (256 <= width <= 3840 and 256 <= height <= 2160):
        return None
    return f"{width}x{height}"

def _number(key: str, label: str, hint: str, low: int, high: int) -> Field:
    return Field(key, label, hint, _whole(low, high), low, high)

FIELDS: dict[str, Field] = {
    "size": Field("size", "sts.qly.size", "sts.typed.size_hint", _size),
    "fps": _number("fps", "sts.qly.fps", "sts.typed.fps_hint", 15, 240),
    "dim": _number("dim", "sts.qly.dim", "sts.typed.percent_hint", 0, 100),
    "blur": _number("blur", "sts.qly.blur", "sts.typed.percent_hint", 0, 100),
    "meter": _number("meter", "sts.qly.meter", "sts.typed.meter_hint", 50, 300),
    "cursor": _number("cursor", "sts.qly.cursor", "sts.typed.cursor_hint", 40, 200),
    "music": _number("music", "sts.snd.music", "sts.typed.percent_hint", 0, 100),
    "hitsounds": _number(
        "hitsounds", "sts.snd.hitsounds", "sts.typed.percent_hint", 0, 100
    ),
    "volume": _number("volume", "sts.snd.volume", "sts.typed.volume_hint", 0, 200),
}

_ASKED: dict[tuple[int, int], tuple[int, str]] = {}

_MOST = 64

def value_button(choices: renders.Choices, key: str, lang: str):
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

        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    if len(_ASKED) > _MOST:

        _ASKED.pop(next(iter(_ASKED)))
    _ASKED[(prompt.chat.id, prompt.message_id)] = (callback.from_user.id, key)
    await callback.answer()

class AnswerFilter(BaseFilter):

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

    for doomed in (message.reply_to_message, message):
        try:
            await doomed.delete()
        except Exception:
            pass
    await message.answer(
        t("sts.typed.set", lang, name=t(field.label, lang), value=_shown(typed_key, value))
    )

__all__ = ["router", "FIELDS", "value_button", "AnswerFilter"]
