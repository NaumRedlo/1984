"""Shared shell for the unified settings menu (`sts`).

Holds what more than one section needs: the owner-binding guard (+ its owner
map), the home/nav keyboards, and the render-settings loader. Each section
module (render_settings, skins, account, titles, renders_library) owns its own
Router and imports these helpers; the package ``__init__`` assembles those
routers under one parent router and registers ``_owner_guard`` there, so the
guard (and its ``lang`` injection) covers every section — exactly as it did
when everything lived in one module.
"""

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_db_session
from utils.i18n import t
from utils.language import get_language
from utils.osu.resolve_user import get_registered_user
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.profile.render import _get_or_create_settings


# Owner-binding: a settings menu (and its callbacks) belongs to the user who
# opened it. In a group the message is visible to everyone, so without this a
# bystander could tap your buttons and drive (and mutate) settings on your card.
# Maps (chat_id, message_id) -> opener tg_id; checked by the guard below.
_MENU_OWNERS: dict = {}
_MENU_OWNERS_CAP = 2000


def _remember_owner(chat_id: int, message_id: int, tg_id: int) -> None:
    if len(_MENU_OWNERS) >= _MENU_OWNERS_CAP:
        # Drop the oldest ~half; menus are short-lived so this is cheap and rare.
        for k in list(_MENU_OWNERS)[: _MENU_OWNERS_CAP // 2]:
            _MENU_OWNERS.pop(k, None)
    _MENU_OWNERS[(chat_id, message_id)] = tg_id


def _is_foreign_menu_tap(data, chat_id, message_id, from_id) -> bool:
    """True if this is an `st:*` tap on a settings menu owned by someone else.
    Unknown owner (e.g. after a restart) returns False — each callback still
    resolves the caller's own data, so the worst case is cosmetic."""
    if not (data and data.startswith("st:")):
        return False
    owner = _MENU_OWNERS.get((chat_id, message_id))
    return owner is not None and owner != from_id


async def _owner_guard(handler, event, data):
    """Block foreign taps on a settings menu (group chats — the message is visible
    to everyone). Also injects `lang` (the tapper's own language) for every
    callback handler on this router, so they don't each need their own
    get_language() call."""
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
        [InlineKeyboardButton(text=t("sts.kb.render", lang), callback_data="st:render")],
        [InlineKeyboardButton(text=t("sts.kb.my_renders", lang), callback_data="st:rnd")],
        [InlineKeyboardButton(text=t("sts.kb.account", lang), callback_data="st:acc")],
        [InlineKeyboardButton(text=t("sts.kb.title", lang), callback_data="st:tt")],
        [InlineKeyboardButton(text=t("sts.kb.language", lang), callback_data="st:lang")],
        [InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close")],
    ])


def _nav_row(lang: str = "en") -> list:
    return [
        InlineKeyboardButton(text=t("sts.kb.back", lang), callback_data="st:home"),
        InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
    ]


def _render_back_row(lang: str = "en") -> list:
    return [
        InlineKeyboardButton(text=t("sts.kb.back", lang), callback_data="st:render"),
        InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
    ]


async def _load_settings(callback: types.CallbackQuery, tenant_chat_id, lang: str = "en"):
    """Resolve the caller's render settings (or None + alert if not registered).
    The instance stays usable after the session closes — attributes are loaded."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return None
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return None
        return await _get_or_create_settings(session, user.id)
