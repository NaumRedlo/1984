from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.handlers.bsk.common import dm
from db.database import get_db_session
from utils.osu.resolve_user import get_any_user_by_telegram_id

router = Router(name="bsk.pick")


@router.callback_query(F.data.startswith("bskpick:"))
async def on_bsk_pick(callback: CallbackQuery):
    """Handle a player's map pick during the pick phase."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Неверный формат.", show_alert=True)
        return

    duel_id = int(parts[1])
    beatmap_id = int(parts[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return

    result = await dm.submit_pick(callback.bot, duel_id, user.id, beatmap_id)

    if result == 'done':
        await callback.answer("✅ Карта выбрана — раунд начинается!", show_alert=False)
    elif result == 'not_your_turn':
        await callback.answer("Сейчас выбирает соперник.", show_alert=True)
    elif result == 'already':
        await callback.answer("Вы уже сделали выбор в этом ходу.", show_alert=True)
    else:
        await callback.answer("Сейчас нельзя выбрать карту.", show_alert=True)
