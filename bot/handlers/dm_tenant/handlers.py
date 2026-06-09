"""DM group selection: which group's data the bot acts on in a private chat.

The bot is multi-tenant by ``users.chat_id`` (group). In a private chat there is
no group, so the user picks one of the groups they're registered in; the choice
is stored (``utils.tenant.set_dm_tenant``) and applied to every data-scoped
command in that DM. ``ensure_dm_tenant`` is the gate the data handlers call;
``prompt_tenant_pick`` renders the chooser; ``group``/``switch`` re-opens it.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from bot.filters import TextTriggerFilter
from db.database import get_db_session
from utils.group_label import group_label
from utils.logger import get_logger
from utils.tenant import set_dm_tenant, user_tenants

logger = get_logger(__name__)

router = Router(name="dm_tenant")


def _is_private(chat) -> bool:
    return chat is not None and chat.type == "private"


async def prompt_tenant_pick(bot, chat_id: int, telegram_id: int, session) -> None:
    """Show the DM group chooser (or auto-pick / nudge to register).

    - 0 groups → tell the user to register in a group first.
    - 1 group  → auto-select it and confirm.
    - ≥2       → inline buttons, one per group (titles via ``group_label``).
    """
    tenants = await user_tenants(session, telegram_id)

    if not tenants:
        await bot.send_message(
            chat_id,
            "Вы пока не зарегистрированы ни в одной беседе.\n"
            "Зайдите в беседу с ботом и отправьте <code>register &lt;ник&gt;</code>, "
            "затем вернитесь сюда.",
            parse_mode="HTML",
        )
        return

    if len(tenants) == 1:
        only = tenants[0]
        await set_dm_tenant(session, telegram_id, only)
        label = await group_label(bot, only)
        await bot.send_message(
            chat_id,
            f"Использую данные беседы <b>{label}</b>.\n"
            "Сменить позже — команда <code>group</code>.",
            parse_mode="HTML",
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    for t in tenants:
        label = await group_label(bot, t)
        rows.append([InlineKeyboardButton(text=label, callback_data=f"dmtenant:set:{t}")])
    await bot.send_message(
        chat_id,
        "В какой беседе показывать ваши данные? Выберите группу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def ensure_dm_tenant(event, tenant_chat_id) -> bool:
    """Gate for data handlers. ``True`` if a data scope is available.

    In a group ``tenant_chat_id`` is always set → ``True`` (no-op). In a private
    chat with no selection → show the picker and return ``False`` so the handler
    stops. Safe to call with any Message/CallbackQuery; opens its own session.
    """
    if tenant_chat_id is not None:
        return True
    chat = event.chat if isinstance(event, Message) else (
        event.message.chat if isinstance(event, CallbackQuery) and event.message else None)
    if _is_private(chat) and event.from_user is not None:
        if isinstance(event, CallbackQuery):
            await event.answer("Сначала выберите беседу.", show_alert=True)
        async with get_db_session() as session:
            await prompt_tenant_pick(event.bot, chat.id, event.from_user.id, session)
    return False


@router.callback_query(F.data.startswith("dmtenant:set:"))
async def on_tenant_set(callback: CallbackQuery):
    try:
        chat_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный выбор.", show_alert=True)
        return

    async with get_db_session() as session:
        # Defence-in-depth: only accept a group the user is actually registered
        # in — never trust the callback-supplied chat_id blindly.
        allowed = await user_tenants(session, callback.from_user.id)
        if chat_id not in allowed:
            await callback.answer("Эта беседа недоступна.", show_alert=True)
            return
        await set_dm_tenant(session, callback.from_user.id, chat_id)
        label = await group_label(callback.bot, chat_id)

    await callback.answer("Готово.")
    try:
        await callback.message.edit_text(
            f"Использую данные беседы <b>{label}</b>.\n"
            "Сменить позже — команда <code>group</code>.\n"
            "Теперь повторите свою команду.",
            parse_mode="HTML",
        )
    except Exception:
        await callback.bot.send_message(
            callback.from_user.id,
            f"Использую данные беседы <b>{label}</b>.",
            parse_mode="HTML",
        )


@router.message(TextTriggerFilter("group", "switch"), F.chat.type == "private")
async def on_group_switch(message: Message, **_):
    """Re-open the group chooser in a DM (always, even with one group)."""
    async with get_db_session() as session:
        await prompt_tenant_pick(message.bot, message.chat.id, message.from_user.id, session)
