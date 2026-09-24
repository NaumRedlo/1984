from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from utils.language import get_language

_MENU_OWNERS: dict = {}
_MENU_OWNERS_CAP = 2000

def _remember_owner(chat_id: int, message_id: int, tg_id: int) -> None:
    if len(_MENU_OWNERS) >= _MENU_OWNERS_CAP:

        for k in list(_MENU_OWNERS)[: _MENU_OWNERS_CAP // 2]:
            _MENU_OWNERS.pop(k, None)
    _MENU_OWNERS[(chat_id, message_id)] = tg_id

def _is_foreign_menu_tap(data, chat_id, message_id, from_id) -> bool:
    if not (data and data.startswith("st:")):
        return False
    owner = _MENU_OWNERS.get((chat_id, message_id))
    return owner is not None and owner != from_id

async def _owner_guard(handler, event, data):
    lang = (await get_language(event.from_user.id)).lower() if event.from_user else "en"
    data["lang"] = lang
    if isinstance(event, types.CallbackQuery) and event.message is not None:
        if _is_foreign_menu_tap(event.data, event.message.chat.id,
                                event.message.message_id, event.from_user.id):
            await event.answer(t("sts.foreign_menu", lang), show_alert=True)
            return
    return await handler(event, data)

def _home_kb(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.account", lang), callback_data="st:acc")],
        [InlineKeyboardButton(text=t("sts.kb.title", lang), callback_data="st:tt")],
        [InlineKeyboardButton(text=t("sts.kb.skin", lang), callback_data="st:skin")],
        [InlineKeyboardButton(text=t("sts.kb.language", lang), callback_data="st:lang")],
        [InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close")],
    ])

def _nav_row(lang: str = "en") -> list:
    return [
        InlineKeyboardButton(text=t("sts.kb.back", lang), callback_data="st:home"),
        InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
    ]
