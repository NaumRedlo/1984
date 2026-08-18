"""Shared shell for the unified settings menu (`sts`).

Holds what more than one section needs: the owner-binding guard (+ its owner
map), the home/nav keyboards, and reading and writing somebody's render
settings — the render screen and its sub-tabs both do the last, and putting it
in either of them would have made the other import it in a circle. Each section module (account, titles) owns its
own Router and imports these helpers; the package ``__init__`` assembles those
routers under one parent router and registers ``_owner_guard`` there, so the
guard (and its ``lang`` injection) covers every section.
"""

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_db_session
from utils.i18n import t
from utils.language import get_language
from utils.osu.resolve_user import get_registered_user
from bot.handlers.dossier import renders


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
        [InlineKeyboardButton(text=t("sts.kb.account", lang), callback_data="st:acc")],
        [InlineKeyboardButton(text=t("sts.kb.title", lang), callback_data="st:tt")],
        [InlineKeyboardButton(text=t("sts.kb.language", lang), callback_data="st:lang")],
        [InlineKeyboardButton(text=t("sts.kb.render", lang), callback_data="st:rnd")],
        [InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close")],
    ])


def _nav_row(lang: str = "en") -> list:
    return [
        InlineKeyboardButton(text=t("sts.kb.back", lang), callback_data="st:home"),
        InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
    ]


async def _load(tg_id: int, tenant_chat_id) -> renders.Choices:
    """This person's settings, from their row when they have one.

    Read on the way into the screen rather than held for ever: the bot may have
    restarted since they last set anything, and a settings screen showing
    defaults over stored values is worse than one that is slow to open.
    """
    choices = renders.choices(tg_id)
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        return renders.restore_settings(user, choices)


async def _store(tg_id: int, tenant_chat_id, choices: renders.Choices) -> None:
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        if user:
            renders.remember_settings(user, choices)
            await session.commit()


def switch_row(choices: renders.Choices, keys: tuple[str, ...], lang: str) -> list:
    """Switches, one button apiece, each saying what it *is*.

    Ticked or not, rather than offering both halves of a yes/no as two buttons —
    twice the width to say the same thing. Shared with the sound sub-tab, which
    draws `mute` the same way.
    """
    return [
        InlineKeyboardButton(
            text=f"{'☑️' if getattr(choices, key) else '⬜️'} {t(f'sts.rnd.{key}', lang)}",
            callback_data=f"st:rnd:{key}:{'0' if getattr(choices, key) else '1'}",
        )
        for key in keys
    ]
