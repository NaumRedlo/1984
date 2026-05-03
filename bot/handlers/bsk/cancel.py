from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
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
    - Pending: only the challenger can cancel.
    - Accepted / round_active: either player can cancel (forfeits the duel).
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

    result = await dm.cancel_duel(message.bot, duel.id, user.id)

    if result == 'cancelled':
        await message.answer("Дуэль отменена.")
    elif result == 'not_challenger':
        await message.answer("Отменить вызов может только тот, кто его отправил.")
    else:
        await message.answer("Не удалось отменить дуэль.")


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
