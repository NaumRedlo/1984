"""Render sub-tab (`st:snd`): how loud each half of the mix is.

`Без звука` on the render screen is all-or-nothing, and all-or-nothing is not
what somebody wants when they cannot hear the play over the song. So this: the
map's own track and the hit sounds, each a level of its own, the way the game
states a volume.

A sub-tab beside the movement ones rather than two more rows on the render
screen — same reason, and the same prefix rule: `st:rnd:` ends in a catch-all
that reads any four-part callback as a setting, so this uses `st:snd`.

Steps rather than a slider, because Telegram has no slider. Five of them per
row, which is as many buttons as a row can hold and still be read.
"""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu import typed
from bot.handlers.profile.settings_menu.common import (
    _load, _store, sub_nav_row, switch_row,
)

router = Router(name="settings_render_sound")

# The two halves, by the name they have on `Choices` and on the engine's own
# flags — `--music` and `--hitsounds` take the same numbers.
HALVES: tuple[str, ...] = ("music", "hitsounds")


def tab_button(lang: str = "en") -> InlineKeyboardButton:
    """The button the render screen shows beside the movement sub-tabs."""
    return InlineKeyboardButton(text=t("sts.snd.tab", lang), callback_data="st:snd")


def _kb(choices: renders.Choices, lang: str) -> InlineKeyboardMarkup:
    # One row apiece, and the value is typed rather than picked. A row of five
    # percentages could not say 63, took a line and a half of the screen, and
    # grew a button every time somebody wanted a figure it did not have — see
    # `typed.py` for how the asking is kept from eating a group chat.
    rows = [
        [typed.value_button(choices, half, lang)]
        for half in (*HALVES, "volume")
    ]
    # Silence belongs with the levels rather than a screen away: it is the same
    # question — how loud — asked at its far end, and somebody who turned the
    # music down to nothing and wants no sound at all should not have to go
    # looking for the switch that says so.
    # The map's own hit sounds, and silence. Both belong here: one is a question
    # about *whose* sounds and the other about whether there are any, and both
    # are questions about sound.
    rows.append(switch_row(choices, ("map_hitsounds", "mute"), lang))
    rows.append(sub_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text(choices: renders.Choices, lang: str) -> str:
    body = t("sts.snd.body", lang)
    if choices.mute:
        # Said where it applies: somebody who muted the render months ago and
        # then came here to turn the music down would otherwise set a level
        # that changes nothing and hear no difference.
        body += "\n\n" + t("sts.snd.muted", lang)
    return body


async def show(callback: types.CallbackQuery, choices: renders.Choices, lang: str) -> None:
    """Redraw this screen — for the render section, which owns the switch
    handler that `mute` still goes through."""
    await _draw(callback, choices, lang)


async def _draw(callback: types.CallbackQuery, choices: renders.Choices, lang: str) -> None:
    try:
        await callback.message.edit_text(
            _text(choices, lang), parse_mode="HTML", reply_markup=_kb(choices, lang)
        )
    except Exception:  # noqa: BLE001 — an unchanged message is not an error
        pass


@router.callback_query(F.data == "st:snd")
async def cb_sound(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    """Open the screen. The levels on it are typed, not tapped — `typed.py`
    owns both the asking and the storing, so there is nothing here to set."""
    choices = await _load(callback.from_user.id, tenant_chat_id)
    await _draw(callback, choices, lang)
    await callback.answer()


__all__ = ["router", "show", "tab_button", "HALVES"]
