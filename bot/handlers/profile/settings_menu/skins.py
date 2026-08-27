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
from bot.handlers.profile.settings_menu.common import _load, _store, sub_nav_row
from dossier import skins as store

router = Router(name="settings_render_skins")

# What the engine draws in when nobody has chosen. Named here rather than
# spelled as a bare string in three places.
DEFAULT_SKIN = "classic"


def tab_button(lang: str = "en") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("sts.skn.tab", lang), callback_data="st:skn")


def rows(choices: renders.Choices, lang: str, tg_id: int | None = None) -> list:
    """The stored skins, three to a row, under two headings.

    Listed rather than typed: a skin arrives by sending the bot an `.osk`, and
    asking somebody to then remember its name would be a worse way to pick one
    than showing them. Three across because one per row turned a screen with a
    handful of skins into a scroll.

    **Yours** are the ones you sent, **shared** is everything anybody sent —
    and once a store has fifty skins in it, the four you uploaded are the four
    you actually want and finding them in an alphabetical list of fifty is the
    problem this solves. A person who has sent none still gets both headings:
    the shape of the picker should not depend on what you happen to own.

    The engine's own look leads the shared list rather than getting a heading
    to itself. It belongs to nobody, which is what shared means.
    """
    current = choices.skin or DEFAULT_SKIN
    # Which of these were unpacked by code older than what is running. A skin
    # folder is made once and used for ever, so a fix to the unpacking does
    # nothing for the skins already here — and the store keeps no `.osk` to
    # redo them from, so the only way back is somebody sending the archive
    # again. Marking them is what makes that possible to ask for.
    stale = set(store.stale())

    def button(name: str) -> InlineKeyboardButton:
        shown = t("sts.rnd.skin_default", lang) if name == DEFAULT_SKIN else name
        if name in stale:
            shown = f"{shown} ⚠"
        return InlineKeyboardButton(
            text=f"{'● ' if name == current else ''}{shown}",
            # The name is checked against the store when it is used, so a
            # stale keyboard naming a deleted skin fails rather than
            # resolving to a path.
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
        # Said rather than left blank: an empty stretch under a heading reads
        # as something that failed to load.
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
    """Redraw this screen — for the render section, which owns the handler a
    skin choice still goes through."""
    try:
        await callback.message.edit_text(
            t("sts.skn.body", lang),
            parse_mode="HTML",
            reply_markup=_kb(choices, lang, callback.from_user.id),
        )
    except Exception:  # noqa: BLE001 — an unchanged message is not an error
        pass


@router.callback_query(F.data == "st:skn")
async def cb_skins(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    choices = await _load(callback.from_user.id, tenant_chat_id)
    await show(callback, choices, lang)
    await callback.answer()


__all__ = ["router", "show", "tab_button", "rows", "DEFAULT_SKIN"]
