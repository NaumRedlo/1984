from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _load, _store, sub_nav_row
from dossier import skins as store

router = Router(name="settings_render_skins")

DEFAULT_SKIN = "classic"

def tab_button(lang: str = "en") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("sts.skn.tab", lang), callback_data="st:skn")

def rows(choices: renders.Choices, lang: str, tg_id: int | None = None) -> list:
    current = choices.skin or DEFAULT_SKIN

    stale = set(store.stale())

    def button(name: str) -> InlineKeyboardButton:
        shown = t("sts.rnd.skin_default", lang) if name == DEFAULT_SKIN else name
        if name in stale:
            shown = f"{shown} ⚠"
        return InlineKeyboardButton(
            text=f"{'● ' if name == current else ''}{shown}",

            callback_data=f"st:rnd:skin:{name}"[:64],
        )

    def heading(key: str) -> list:
        return [InlineKeyboardButton(text=t(key, lang), callback_data="st:rnd:noop")]

    mine, shared = store.by_owner(tg_id)
    out = [heading("sts.skn.mine")]
    if mine:
        buttons = [button(name) for name in mine]
        out += [buttons[at:at + 3] for at in range(0, len(buttons), 3)]
    else:

        out.append([
            InlineKeyboardButton(
                text=t("sts.skn.none_yours", lang), callback_data="st:rnd:noop"
            )
        ])
    out.append(heading("sts.skn.shared"))
    buttons = [button(name) for name in [DEFAULT_SKIN, *shared]]
    out += [buttons[at:at + 3] for at in range(0, len(buttons), 3)]
    return out

def _kb(choices: renders.Choices, lang: str, tg_id: int | None = None) -> InlineKeyboardMarkup:
    keyboard = rows(choices, lang, tg_id)
    keyboard.append(sub_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def show(callback: types.CallbackQuery, choices: renders.Choices, lang: str) -> None:
    try:
        await callback.message.edit_text(
            t("sts.skn.body", lang),
            parse_mode="HTML",
            reply_markup=_kb(choices, lang, callback.from_user.id),
        )
    except Exception:
        pass

@router.callback_query(F.data == "st:skn")
async def cb_skins(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    choices = await _load(callback.from_user.id, tenant_chat_id)
    await show(callback, choices, lang)
    await callback.answer()

__all__ = ["router", "show", "tab_button", "rows", "DEFAULT_SKIN"]
