import asyncio

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.filters import TextTriggerFilter
from bot.handlers.bsk.common import dm
from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from utils.osu.resolve_user import get_any_user_by_telegram_id

router = Router(name="bsk.cancel")


@router.message(TextTriggerFilter("bskcancel", "bskc"))
async def cmd_bsk_cancel(message: Message):
    """Cancel your active BSK duel.
    - Test duels: cancel immediately.
    - Real duels: request confirmation from the other player (60s timeout).
    """
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        duel = (await session.execute(
            select(BskDuel).where(
                BskDuel.status.in_(["pending", "accepted", "round_active"]),
                (BskDuel.player1_user_id == user.id) | (BskDuel.player2_user_id == user.id),
            )
        )).scalar_one_or_none()

    if not duel:
        await message.answer("У вас нет активной дуэли, которую можно отменить.")
        return

    if duel.is_test or duel.player1_user_id == duel.player2_user_id:
        result = await dm.cancel_duel(message.bot, duel.id, user.id)
        await message.answer("Дуэль отменена." if result == 'cancelled' else "Не удалось отменить.")
        return

    other_id = duel.player2_user_id if user.id == duel.player1_user_id else duel.player1_user_id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Подтвердить отмену",
            callback_data=f"bskd:confirm_cancel:{duel.id}:{other_id}",
        )
    ]])
    await message.answer(
        "⚠️ Запрос на отмену дуэли. Ожидаем подтверждение от соперника (60 сек)...",
        reply_markup=kb,
        parse_mode="HTML",
    )

    asyncio.create_task(_auto_cancel_after(message.bot, duel.id, user.id, 60))


async def _auto_cancel_after(bot, duel_id: int, user_id: int, timeout: int):
    await asyncio.sleep(timeout)
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status not in ('pending', 'accepted', 'round_active'):
            return
    await dm.cancel_duel(bot, duel_id, user_id)


@router.callback_query(F.data.startswith("bskd:confirm_cancel:"))
async def on_confirm_cancel(callback: CallbackQuery):
    parts = callback.data.split(":")
    duel_id = int(parts[2])
    expected_user_id = int(parts[3])

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, callback.from_user.id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return

    if user.id != expected_user_id:
        await callback.answer("Только соперник может подтвердить отмену.", show_alert=True)
        return

    result = await dm.cancel_duel(callback.bot, duel_id, user.id)
    if result == 'cancelled':
        await callback.answer("Дуэль отменена.")
        await callback.message.edit_text("❌ Дуэль отменена по согласию обоих игроков.")
    else:
        await callback.answer("Не удалось отменить дуэль.", show_alert=True)


@router.callback_query(F.data.startswith("bskd:test_cancel:"))
async def on_bskd_test_cancel(callback: CallbackQuery):
    """Cancel a test duel via inline button."""
    duel_id = int(callback.data.split(":")[-1])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return

    ok = await dm.cancel_test_duel(callback.bot, duel_id, user.id)
    if ok:
        await callback.answer("Тестовая дуэль отменена.", show_alert=False)
    else:
        await callback.answer("Нельзя отменить эту дуэль.", show_alert=True)
