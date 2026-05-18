from aiogram import Router, types
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from utils.admin_check import AdminFilter
from utils.osu.resolve_user import get_any_user_by_telegram_id
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_bsk_test")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

@router.message(TextTriggerFilter("bsktest"))
async def cmd_bsk_test(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """bsktest [casual|ranked] — start a test duel as both players."""
    args = (trigger_args.args or "").strip().lower()
    mode = "casual" if args not in ("ranked",) else "ranked"

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

    from services.bsk.duel_manager import create_test_duel
    duel = await create_test_duel(
        bot=message.bot,
        chat_id=message.chat.id,
        user_id=user.id,
        mode=mode,
        osu_api=osu_api_client,
        # Test duels stay in the topic where the admin invoked them — they
        # ignore BSK_DUEL_THREAD_ID so they don't pollute the public duel feed.
        thread_id=getattr(message, "message_thread_id", None),
    )
    if not duel:
        await message.answer("Не удалось создать тестовую дуэль. Убедитесь что в пуле есть карты.")


@router.message(TextTriggerFilter("bsktestround", "bsktr"))
async def cmd_bsk_test_round(message: types.Message, trigger_args: TriggerArgs):
    """bsktestround [p1_pp p1_acc p2_pp p2_acc] — simulate round with fake scores."""
    args = (trigger_args.args or "").strip().split()

    # Defaults
    p1_pp, p1_acc, p2_pp, p2_acc = 300.0, 97.5, 280.0, 96.0
    try:
        if len(args) >= 4:
            p1_pp, p1_acc, p2_pp, p2_acc = float(args[0]), float(args[1]), float(args[2]), float(args[3])
        elif len(args) == 2:
            p1_pp, p2_pp = float(args[0]), float(args[1])
    except ValueError:
        await message.answer("Использование: <code>bsktestround [p1_pp p1_acc p2_pp p2_acc]</code>", parse_mode="HTML")
        return

    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        from db.models.bsk_duel import BskDuel as _BskDuel
        duel = (await session.execute(
            select(_BskDuel).where(
                _BskDuel.is_test == True,
                _BskDuel.status == 'round_active',
                (_BskDuel.player1_user_id == user.id) | (_BskDuel.player2_user_id == user.id),
            )
        )).scalar_one_or_none()

    if not duel:
        await message.answer("Нет активной тестовой дуэли. Запустите <code>bsktest</code>.", parse_mode="HTML")
        return

    from services.bsk.duel_manager import simulate_test_round
    ok = await simulate_test_round(
        bot=message.bot,
        duel_id=duel.id,
        p1_pp=p1_pp, p1_acc=p1_acc, p1_combo_ratio=0.95, p1_misses=1,
        p2_pp=p2_pp, p2_acc=p2_acc, p2_combo_ratio=0.90, p2_misses=2,
    )
    if not ok:
        await message.answer("Не удалось симулировать раунд.")


@router.message(TextTriggerFilter("bsktestroom", "bsktrm"))
async def cmd_bsk_test_room(message: types.Message):
    """bsktestroom — create IRC room for the active test duel."""
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        from db.models.bsk_duel import BskDuel as _BskDuel
        duel = (await session.execute(
            select(_BskDuel).where(
                _BskDuel.is_test == True,
                _BskDuel.status.in_(['accepted', 'round_active']),
                (_BskDuel.player1_user_id == user.id) | (_BskDuel.player2_user_id == user.id),
            )
        )).scalar_one_or_none()

        if not duel:
            await message.answer("Нет активной тестовой дуэли.")
            return

        if duel.osu_match_id:
            await message.answer(f"Комната уже создана: https://osu.ppy.sh/mp/{duel.osu_match_id}")
            return

        from services.bancho_irc import get_irc_client
        from services.bsk.irc_room import create_duel_room
        irc = get_irc_client()
        if not irc.connected:
            await message.answer("IRC не подключён.")
            return

        match_id = await create_duel_room(
            irc, duel.id, user.osu_username, user.osu_username,
            mode=duel.mode, is_test=True,
        )
        if not match_id:
            await message.answer("Не удалось создать комнату.")
            return

        duel.osu_match_id = str(match_id)
        await session.commit()

    await message.answer(f"Комната создана: https://osu.ppy.sh/mp/{match_id}")


@router.message(TextTriggerFilter("bsktestend", "bskte"))
async def cmd_bsk_test_end(message: types.Message):
    """bsktestend — cancel active test duel."""
    async with get_db_session() as session:
        user = await get_any_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("Вы не зарегистрированы.")
            return

        from db.models.bsk_duel import BskDuel as _BskDuel
        duel = (await session.execute(
            select(_BskDuel).where(
                _BskDuel.is_test == True,
                _BskDuel.status.in_(['pending', 'accepted', 'round_active']),
                (_BskDuel.player1_user_id == user.id) | (_BskDuel.player2_user_id == user.id),
            )
        )).scalar_one_or_none()

        if not duel:
            await message.answer("Нет активной тестовой дуэли.")
            return

        duel.status = 'cancelled'
        await session.commit()

    await message.answer("Тестовая дуэль отменена.")

