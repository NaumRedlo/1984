"""Render sub-tabs (`st:fx`): the engine's optional movements, one at a time.

The render screen answers "how big, how fast, which skin". These answer "what
moves", and they are a different question with a different shape: five switches
that nobody wants presented as five more rows on a screen that already has
eleven. So they get sub-tabs — one per part of the play the movement belongs
to — and the render screen gets one row pointing at them.

One screen, not three. They were grouped by where you look — sliders, cursor,
notes — which is the right grouping and the wrong shape: five switches split
across three taps is more navigation than the five switches are worth. Grouped
still, but as rows on one screen, so both halves of a pair sit side by side and
the whole set is one tap away.

The prefix is `st:fx` and not `st:rnd:fx` on purpose: the render section ends in
a catch-all on `st:rnd:` that reads any four-part callback as a setting, and a
sub-tab arriving there would be answered with "no such setting". A separate
prefix means the two cannot collide however the routers are ordered.
"""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _load, _nav_row, _store

router = Router(name="settings_render_effects")


# Every switch the engine has, with the name it takes on `--effects`, the
# sub-tab it belongs to, and whether it is on when nobody has said.
#
# The defaults are the engine's, and the engine is the authority on them —
# `Effects` in dossier/crates/dossier-render/src/skin.rs. They are repeated here
# because a settings screen has to draw the boxes before anything has been
# stored, and `tests/unit/test_settings_render.py` reads that file to check the
# two have not drifted.
SWITCHES: tuple[tuple[str, str, bool], ...] = (
    ("snake-in", "slider", False),
    ("snake-out", "slider", False),
    ("cursor-expand", "cursor", False),
    ("cursor-trail", "cursor", True),
    ("keypad", "keys", True),
    ("key-bars", "keys", True),
    ("hit-lighting", "note", False),
)

# The groups, in the order the screen shows them. Kept as an ordering rather
# than as separate screens: what they buy is that a pair reads as a pair.
GROUPS: tuple[str, ...] = ("slider", "cursor", "keys", "note")

# One screen, so one name.
TAB = "play"


def _defaults() -> set[str]:
    return {name for name, _, on in SWITCHES if on}


def _on(choices: renders.Choices) -> set[str]:
    """Which movements are on for this person.

    `None` is somebody who has never opened these screens, and the answer for
    them is the engine's own defaults. The empty string is somebody who came in
    and switched all five off, and that is not the same thing — it is obeyed.
    """
    if choices.effects is None:
        return _defaults()
    return {part.strip() for part in choices.effects.split(",") if part.strip()}


def _store_set(choices: renders.Choices, on: set[str]) -> None:
    """Write the set back as the engine's list.

    Always the full list, never a difference from the defaults: what is stored
    has to still mean the same thing after the engine changes its mind about a
    default, and a stored difference would silently follow it.
    """
    choices.effects = ",".join(name for name, _, _ in SWITCHES if name in on)


def _tab_of(name: str) -> str | None:
    for switch, tab, _ in SWITCHES:
        if switch == name:
            return tab
    return None


def tab_button(lang: str = "en") -> InlineKeyboardButton:
    """The button the render screen shows."""
    return InlineKeyboardButton(text=t("sts.fx.tab", lang), callback_data=f"st:fx:{TAB}")


def _kb(choices: renders.Choices, lang: str) -> InlineKeyboardMarkup:
    on = _on(choices)
    rows = [
        [
            InlineKeyboardButton(
                # A tick means the movement named is happening, the same way the
                # render screen's switches read.
                text=f"{'☑️' if name in on else '⬜️'} {t(f'sts.fx.{name}', lang)}",
                callback_data=f"st:fx:{TAB}:{name}",
            )
        ]
        for group in GROUPS
        for name, belongs, _ in SWITCHES
        if belongs == group
    ]
    # Back to the render screen rather than to the settings home: these were
    # opened from there, and a sub-tab that returns somewhere else is a sub-tab
    # you have to navigate back into to change the switch beside the one you
    # just changed.
    rows.append(
        [InlineKeyboardButton(text=t("sts.fx.back", lang), callback_data="st:rnd")]
    )
    rows.append(_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text(lang: str) -> str:
    return "\n\n".join(
        [t("sts.fx.body", lang)] + [t(f"sts.fx.about.{group}", lang) for group in GROUPS]
    )


async def _draw(callback: types.CallbackQuery, choices: renders.Choices, lang: str) -> None:
    try:
        await callback.message.edit_text(
            _text(lang), parse_mode="HTML", reply_markup=_kb(choices, lang)
        )
    except Exception:  # noqa: BLE001 — an unchanged message is not an error
        pass


@router.callback_query(F.data.startswith("st:fx:"))
async def cb_effects(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":")
    if len(parts) < 3 or parts[2] != TAB:
        # A keyboard can outlive the screen it was drawn for, and the three
        # sub-tabs this replaces left theirs in people's chats.
        await callback.answer(t("sts.rnd.unknown", lang), show_alert=True)
        return

    choices = await _load(callback.from_user.id, tenant_chat_id)
    if len(parts) == 3:
        await _draw(callback, choices, lang)
        await callback.answer()
        return
    if len(parts) != 4:
        await callback.answer()
        return

    name = parts[3]
    if _tab_of(name) is None:
        await callback.answer(t("sts.rnd.unknown", lang), show_alert=True)
        return

    on = _on(choices)
    on.symmetric_difference_update({name})
    _store_set(choices, on)
    await _store(callback.from_user.id, tenant_chat_id, choices)
    await callback.answer(
        t("sts.fx.now_on" if name in on else "sts.fx.now_off", lang,
          name=t(f"sts.fx.{name}", lang))
    )
    await _draw(callback, choices, lang)


__all__ = ["router", "tab_button", "SWITCHES", "GROUPS", "TAB"]
