from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.profile.settings_menu.common import _nav_row
from services.render_farm import skins
from utils.i18n import t

router = Router(name="settings_skin")

def _keyboard(names: list[str], current: str, lang: str) -> InlineKeyboardMarkup:
    rows = []
    default = t("sts.skin.default", lang)
    rows.append([InlineKeyboardButton(text=("✓ " if not current else "") + default, callback_data="st:skin:-")])
    for at, name in enumerate(names):
        mark = "✓ " if name == current else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{name}"[:60], callback_data=f"st:skin:{at}")])
    rows.append(_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _show(callback: types.CallbackQuery, lang: str) -> None:
    names = skins.available()
    current = await skins.chosen_name(callback.from_user.id) or ""
    if current not in names:
        current = ""
    shown = current or t("sts.skin.default", lang)
    body = t("sts.skin.home", lang, skin=shown)
    if not names:
        body += "\n\n" + t("sts.skin.none_here", lang)
    try:
        await callback.message.edit_text(body, reply_markup=_keyboard(names, current, lang), parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "st:skin")
async def cb_skin(callback: types.CallbackQuery, lang: str = "en", **_) -> None:
    await _show(callback, lang)
    await callback.answer()

@router.callback_query(F.data.startswith("st:skin:"))
async def cb_pick(callback: types.CallbackQuery, lang: str = "en", **_) -> None:
    picked = callback.data.split(":", 2)[2]
    names = skins.available()
    if picked == "-":
        await skins.choose(callback.from_user.id, None)
    elif picked.isdigit() and int(picked) < len(names):
        await skins.choose(callback.from_user.id, names[int(picked)])
    await _show(callback, lang)
    await callback.answer(t("sts.skin.saved", lang))
