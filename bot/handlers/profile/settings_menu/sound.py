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
from bot.handlers.profile.settings_menu.common import (
    _load, _nav_row, _store, switch_row,
)

router = Router(name="settings_render_sound")

# The two halves, by the name they have on `Choices` and on the engine's own
# flags — `--music` and `--hitsounds` take the same numbers.
HALVES: tuple[str, ...] = ("music", "hitsounds")

# What a level can be set to. Not every percentage: a menu of a hundred buttons
# is worse than one of five, and nobody has ever wanted 63%.
STEPS: tuple[int, ...] = (0, 25, 50, 75, 100)


def tab_button(lang: str = "en") -> InlineKeyboardButton:
    """The button the render screen shows beside the movement sub-tabs."""
    return InlineKeyboardButton(text=t("sts.snd.tab", lang), callback_data="st:snd")


def _apply(choices: renders.Choices, half: str, level: int) -> bool:
    """Set one half. False when the pair is not one this menu offers — a
    callback is user input, and a keyboard outlives the screen it was drawn
    for."""
    if half not in HALVES or level not in STEPS:
        return False
    setattr(choices, half, level)
    return True


def _kb(choices: renders.Choices, lang: str) -> InlineKeyboardMarkup:
    rows = []
    for half in HALVES:
        current = getattr(choices, half)
        # The label is a row of its own rather than a prefix on the first
        # button: five levels plus a name is six buttons wide, which Telegram
        # renders as six slivers.
        rows.append([
            InlineKeyboardButton(
                text=f"{t(f'sts.snd.{half}', lang)} — {current}%",
                callback_data="st:rnd:noop",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text=f"{'● ' if level == current else ''}{level}%",
                callback_data=f"st:snd:{half}:{level}",
            )
            for level in STEPS
        ])
    # Silence belongs with the levels rather than a screen away: it is the same
    # question — how loud — asked at its far end, and somebody who turned the
    # music down to nothing and wants no sound at all should not have to go
    # looking for the switch that says so.
    # The map's own hit sounds, and silence. Both belong here: one is a question
    # about *whose* sounds and the other about whether there are any, and both
    # are questions about sound.
    rows.append(switch_row(choices, ("map_hitsounds", "mute"), lang))
    rows.append(
        [InlineKeyboardButton(text=t("sts.fx.back", lang), callback_data="st:rnd")]
    )
    rows.append(_nav_row(lang))
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


@router.callback_query(F.data.startswith("st:snd"))
async def cb_sound(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":")
    choices = await _load(callback.from_user.id, tenant_chat_id)
    if len(parts) == 2:
        await _draw(callback, choices, lang)
        await callback.answer()
        return
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer(t("sts.rnd.unknown", lang), show_alert=True)
        return

    if not _apply(choices, parts[2], int(parts[3])):
        await callback.answer(t("sts.rnd.unknown", lang), show_alert=True)
        return
    await _store(callback.from_user.id, tenant_chat_id, choices)
    await callback.answer(f"{t(f'sts.snd.{parts[2]}', lang)} — {parts[3]}%")
    await _draw(callback, choices, lang)


__all__ = ["router", "show", "tab_button", "HALVES", "STEPS"]
