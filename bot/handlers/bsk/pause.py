from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from bot.handlers.bsk.common import dm, pause_keyboard, resume_keyboard
from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from utils.osu.resolve_user import get_any_user_by_telegram_id

router = Router(name="bsk.pause")


@router.callback_query(F.data.startswith("bskd:pause:"))
async def on_bskd_pause(callback: CallbackQuery):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        is_test = duel.is_test if duel else False

    result = await dm.vote_pause(callback.bot, duel_id, user.id)
    if result == 'voted':
        await callback.answer("Вы проголосовали за паузу. Ждём второго игрока.", show_alert=True)
    elif result == 'paused':
        try:
            await callback.message.edit_reply_markup(
                reply_markup=resume_keyboard(duel_id, is_test)
            )
        except Exception:
            pass
        await callback.answer("⏸ Пауза! Форфейт продлён на 15 минут.", show_alert=True)
    elif result == 'already':
        await callback.answer("Вы уже проголосовали за паузу.", show_alert=True)
    else:
        await callback.answer("Нельзя поставить паузу сейчас.", show_alert=True)


@router.callback_query(F.data.startswith("bskd:resume:"))
async def on_bskd_resume(callback: CallbackQuery):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        is_test = duel.is_test if duel else False

    result = await dm.resume_duel(callback.bot, duel_id, user.id)
    if result == 'resumed':
        try:
            await callback.message.edit_reply_markup(
                reply_markup=pause_keyboard(duel_id, is_test)
            )
        except Exception:
            pass
        await callback.answer("▶️ Дуэль возобновлена!", show_alert=False)
    else:
        await callback.answer("Нельзя возобновить сейчас.", show_alert=True)
