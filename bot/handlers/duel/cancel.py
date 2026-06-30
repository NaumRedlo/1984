import asyncio

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.filters import TextTriggerFilter
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.duel.common import dm
from db.database import get_db_session
from db.models.duel import Duel
from utils.aio import spawn
from utils.osu.resolve_user import get_any_user_by_telegram_id

router = Router(name="duel.cancel")


@router.message(TextTriggerFilter("duelc"))
async def cmd_duel_cancel(message: Message, tenant_chat_id=None):
    """Cancel your active DUEL duel.
    - Test duels: cancel immediately.
    - Real duels: request confirmation from the other player (60s timeout).
    """
    tg_id = message.from_user.id

    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id, tenant_chat_id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duel = (await session.execute(
            select(Duel).where(
                Duel.status.in_(["pending", "accepted", "round_active"]),
                (Duel.player1_user_id == user.id) | (Duel.player2_user_id == user.id),
            )
        )).scalar_one_or_none()

    if not duel:
        await message.answer("У вас нет активной дуэли, которую можно отменить.")
        return

    if duel.status == 'pending':
        # Not yet accepted — cancel immediately, no opponent confirmation needed.
        result = await dm.cancel_duel(message.bot, duel.id, user.id,
                                      event_chat_id=tenant_chat_id)
        await message.answer("Дуэль отменена." if result == 'cancelled' else "Не удалось отменить.")
        return

    other_id = duel.player2_user_id if user.id == duel.player1_user_id else duel.player1_user_id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Подтвердить отмену",
            callback_data=f"dueld:confirm_cancel:{duel.id}:{other_id}",
        )
    ]])
    await message.answer(
        "⚠️ Запрос на отмену дуэли. Ожидаем подтверждение от соперника (60 сек)...",
        reply_markup=kb,
        parse_mode="HTML",
    )

    spawn(_auto_cancel_after(message.bot, duel.id, user.id, 60), name=f"duel_auto_cancel_{duel.id}")


async def _auto_cancel_after(bot, duel_id: int, user_id: int, timeout: int):
    await asyncio.sleep(timeout)
    async with get_db_session() as session:
        duel = (await session.execute(
            select(Duel).where(Duel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status not in ('pending', 'accepted', 'round_active'):
            return
    await dm.cancel_duel(bot, duel_id, user_id)


@router.callback_query(F.data.startswith("dueld:confirm_cancel:"))
async def on_confirm_cancel(callback: CallbackQuery, tenant_chat_id=None):
    parts = callback.data.split(":")
    duel_id = int(parts[2])
    expected_user_id = int(parts[3])

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return

    if user.id != expected_user_id:
        await callback.answer("Только соперник может подтвердить отмену.", show_alert=True)
        return

    result = await dm.cancel_duel(callback.bot, duel_id, user.id,
                                  event_chat_id=tenant_chat_id)
    if result == 'cancelled':
        await callback.answer("Дуэль отменена.")
        await callback.message.edit_text("❌ Дуэль отменена по согласию обоих игроков.")
    else:
        await callback.answer("Не удалось отменить дуэль.", show_alert=True)
