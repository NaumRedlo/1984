from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _load, _store, sub_nav_row

router = Router(name="settings_render_effects")

SWITCHES: tuple[tuple[str, str, bool], ...] = (
    ("snake-in", "slider", False),
    ("snake-out", "slider", False),
    ("cursor-expand", "cursor", False),
    ("cursor-trail", "cursor", True),
    ("keypad", "keys", True),
    ("key-bars", "keys", True),
    ("unstable-rate", "hud", True),
    ("hit-lighting", "note", False),
    ("slider-ball-tint", "slider", False),
)

GROUPS: tuple[str, ...] = ("slider", "cursor", "keys", "hud", "note")

TAB = "play"

def _defaults() -> set[str]:
    return {name for name, _, on in SWITCHES if on}

def _on(choices: renders.Choices) -> set[str]:
    if choices.effects is None:
        return _defaults()
    return {part.strip() for part in choices.effects.split(",") if part.strip()}

def _store_set(choices: renders.Choices, on: set[str]) -> None:
    choices.effects = ",".join(name for name, _, _ in SWITCHES if name in on)

def _tab_of(name: str) -> str | None:
    for switch, tab, _ in SWITCHES:
        if switch == name:
            return tab
    return None

def tab_button(lang: str = "en") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("sts.fx.tab", lang), callback_data=f"st:fx:{TAB}")

def _kb(choices: renders.Choices, lang: str) -> InlineKeyboardMarkup:
    on = _on(choices)
    rows: list = []

    for group in GROUPS:
        named = [name for name, belongs, _ in SWITCHES if belongs == group]
        for at in range(0, len(named), 2):
            rows.append([
                InlineKeyboardButton(

                    text=f"{'☑️' if name in on else '⬜️'} {t(f'sts.fx.{name}', lang)}",
                    callback_data=f"st:fx:{TAB}:{name}",
                )
                for name in named[at:at + 2]
            ])

    rows.append(sub_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _text(lang: str) -> str:
    return t("sts.fx.body", lang)

async def _draw(callback: types.CallbackQuery, choices: renders.Choices, lang: str) -> None:
    try:
        await callback.message.edit_text(
            _text(lang), parse_mode="HTML", reply_markup=_kb(choices, lang)
        )
    except Exception:
        pass

@router.callback_query(F.data.startswith("st:fx:"))
async def cb_effects(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":")
    if len(parts) < 3 or parts[2] != TAB:

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
