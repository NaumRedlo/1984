"""Render sub-tab (`st:skn`): which skin a render wears.

A screen of its own because the list grows. It sat on the render screen, where
it was three buttons on the day it was written and is however many `.osk` files
somebody has sent since — a list that pushes everything below it off the bottom
is a list that belongs behind a tap.

The prefix is `st:skn` for the same reason the others have their own: the render
section ends in a catch-all on `st:rnd:` that reads any four-part callback as a
setting.
"""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _load, _nav_row, _store
from services.dossier import skins as store

router = Router(name="settings_render_skins")

# What the engine draws in when nobody has chosen. Named here rather than
# spelled as a bare string in three places.
DEFAULT_SKIN = "classic"


def tab_button(lang: str = "en") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("sts.skn.tab", lang), callback_data="st:skn")


def rows(choices: renders.Choices, lang: str) -> list:
    """The stored skins, three to a row, with the engine's own look first.

    Listed rather than typed: a skin arrives by sending the bot an `.osk`, and
    asking somebody to then remember its name would be a worse way to pick one
    than showing them. Three across because one per row turned a screen with a
    handful of skins into a scroll.
    """
    current = choices.skin or DEFAULT_SKIN
    buttons = []
    for name in [DEFAULT_SKIN, *store.available()]:
        shown = t("sts.rnd.skin_default", lang) if name == DEFAULT_SKIN else name
        buttons.append(
            InlineKeyboardButton(
                text=f"{'● ' if name == current else ''}{shown}",
                # The name is checked against the store when it is used, so a
                # stale keyboard naming a deleted skin fails rather than
                # resolving to a path.
                callback_data=f"st:rnd:skin:{name}"[:64],
            )
        )
    return [buttons[at:at + 3] for at in range(0, len(buttons), 3)]


def _kb(choices: renders.Choices, lang: str) -> InlineKeyboardMarkup:
    keyboard = rows(choices, lang)
    keyboard.append(
        [InlineKeyboardButton(text=t("sts.fx.back", lang), callback_data="st:rnd")]
    )
    keyboard.append(_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def show(callback: types.CallbackQuery, choices: renders.Choices, lang: str) -> None:
    """Redraw this screen — for the render section, which owns the handler a
    skin choice still goes through."""
    try:
        await callback.message.edit_text(
            t("sts.skn.body", lang), parse_mode="HTML", reply_markup=_kb(choices, lang)
        )
    except Exception:  # noqa: BLE001 — an unchanged message is not an error
        pass


@router.callback_query(F.data == "st:skn")
async def cb_skins(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    choices = await _load(callback.from_user.id, tenant_chat_id)
    await show(callback, choices, lang)
    await callback.answer()


__all__ = ["router", "show", "tab_button", "rows", "DEFAULT_SKIN"]
