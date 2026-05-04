"""Handlers for linking an osu! multiplayer match to a BSK duel."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from services.bsk.match_monitor import match_contains_users, parse_match_url
from utils.logger import get_logger
from utils.osu.resolve_user import get_any_user_by_telegram_id

logger = get_logger("bsk.match")
router = Router(name="bsk.match")


class SetMatchStates(StatesGroup):
    waiting_link = State()


@router.callback_query(F.data.startswith("bskd:setmatch:"))
async def on_setmatch_request(callback: CallbackQuery, state: FSMContext):
    duel_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("Сначала зарегистрируйтесь.", show_alert=True)
            return

        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            await callback.answer("Дуэль не найдена.", show_alert=True)
            return
        if user.id not in (duel.player1_user_id, duel.player2_user_id):
            await callback.answer("Это не ваша дуэль.", show_alert=True)
            return
        if duel.status not in ("accepted", "round_active"):
            await callback.answer("Дуэль не активна.", show_alert=True)
            return
        if duel.osu_match_id:
            await callback.answer(
                f"Лобби уже привязано: #{duel.osu_match_id}",
                show_alert=True,
            )
            return

    await state.set_state(SetMatchStates.waiting_link)
    await state.update_data(duel_id=duel_id)

    try:
        await callback.bot.send_message(
            tg_id,
            "📨 <b>Пришлите ссылку на multi-лобби</b>\n\n"
            "Любой из этих форматов:\n"
            "• <code>https://osu.ppy.sh/community/matches/12345</code>\n"
            "• <code>mp #12345</code>\n"
            "• <code>12345</code>\n\n"
            "В лобби должны быть оба игрока дуэли.",
            parse_mode="HTML",
        )
        await callback.answer("Жду ссылку в личке.")
    except Exception as e:
        logger.warning(f"setmatch DM failed for tg_id={tg_id}: {e}")
        await callback.answer(
            "Не удалось написать в личку. Сначала напишите боту /start.",
            show_alert=True,
        )
        await state.clear()


@router.message(SetMatchStates.waiting_link, F.text)
async def on_match_link_received(message: Message, state: FSMContext, osu_api_client):
    data = await state.get_data()
    duel_id = data.get("duel_id")
    if not duel_id:
        await state.clear()
        return

    match_id = parse_match_url(message.text or "")
    if not match_id:
        await message.answer(
            "Не распознал формат. Пришлите ссылку вида "
            "<code>https://osu.ppy.sh/community/matches/12345</code> "
            "или просто <code>12345</code>.",
            parse_mode="HTML",
        )
        return

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            await message.answer("Дуэль не найдена — возможно, уже завершилась.")
            await state.clear()
            return
        if duel.osu_match_id:
            await message.answer(f"Лобби уже было привязано: #{duel.osu_match_id}.")
            await state.clear()
            return

        from utils.osu.resolve_user import get_any_user_by_telegram_id  # local — avoid circular
        user = await get_any_user_by_telegram_id(session, message.from_user.id)
        if not user or user.id not in (duel.player1_user_id, duel.player2_user_id):
            await message.answer("Эта дуэль не ваша.")
            await state.clear()
            return

        from db.models.user import User
        p1 = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one_or_none()
        p2 = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one_or_none()

    try:
        payload = await osu_api_client.get_match(int(match_id))
    except Exception as e:
        logger.warning(f"setmatch: get_match({match_id}) failed: {e}")
        payload = None

    if not payload:
        await message.answer(
            f"Матч <b>#{match_id}</b> не найден или недоступен. Проверьте ссылку.",
            parse_mode="HTML",
        )
        return

    p1_osu = p1.osu_user_id if p1 else None
    p2_osu = p2.osu_user_id if p2 else None
    if not p1_osu or not p2_osu:
        await message.answer("У одного из игроков не привязан osu!-аккаунт.")
        await state.clear()
        return

    if not match_contains_users(payload, p1_osu, p2_osu):
        await message.answer(
            f"В матче <b>#{match_id}</b> не нашёл обоих игроков дуэли. "
            f"Убедитесь, что оба сыграли (или хотя бы зашли в лобби) и попробуйте снова.",
            parse_mode="HTML",
        )
        return

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            await message.answer("Дуэль исчезла.")
            await state.clear()
            return
        if duel.osu_match_id:
            await message.answer(f"Лобби уже было привязано другим игроком: #{duel.osu_match_id}.")
            await state.clear()
            return
        duel.osu_match_id = int(match_id)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Лобби привязано: "
        f"<a href=\"https://osu.ppy.sh/community/matches/{match_id}\">#{match_id}</a>\n"
        f"Играйте карту — бот сам подхватит результаты.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
