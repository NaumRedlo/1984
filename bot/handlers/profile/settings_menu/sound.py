from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu import typed
from bot.handlers.profile.settings_menu.common import (
    _load, _store, sub_nav_row, switch_row,
)

router = Router(name="settings_render_sound")

HALVES: tuple[str, ...] = ("music", "hitsounds")

def tab_button(lang: str = "en") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("sts.snd.tab", lang), callback_data="st:snd")

def _kb(choices: renders.Choices, lang: str) -> InlineKeyboardMarkup:

    rows = [
        [typed.value_button(choices, half, lang)]
        for half in (*HALVES, "volume")
    ]

    rows.append(switch_row(choices, ("map_hitsounds", "mute"), lang))
    rows.append(sub_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _text(choices: renders.Choices, lang: str) -> str:
    body = t("sts.snd.body", lang)
    if choices.mute:

        body += "\n\n" + t("sts.snd.muted", lang)
    return body

async def show(callback: types.CallbackQuery, choices: renders.Choices, lang: str) -> None:
    await _draw(callback, choices, lang)

async def _draw(callback: types.CallbackQuery, choices: renders.Choices, lang: str) -> None:
    try:
        await callback.message.edit_text(
            _text(choices, lang), parse_mode="HTML", reply_markup=_kb(choices, lang)
        )
    except Exception:
        pass

@router.callback_query(F.data == "st:snd")
async def cb_sound(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    choices = await _load(callback.from_user.id, tenant_chat_id)
    await _draw(callback, choices, lang)
    await callback.answer()

__all__ = ["router", "show", "tab_button", "HALVES"]
