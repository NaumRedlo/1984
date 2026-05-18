"""BSK test duel helpers."""
import asyncio
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot
from sqlalchemy import select, update as sa_update

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.user import User
from services.bsk.composite import composite_score, composite_points
from services.bsk.duel_constants import TARGET_SCORE, _target_score_for_mode
from services.bsk.duel_state import pool_state as _pool_state, ban_state as _ban_state
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.telegram_safe import safe_edit_text

logger = get_logger("bsk.duel_test")


async def _get_user(session, user_id: int):
    return (await session.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()

async def create_test_duel(
    bot: Bot,
    chat_id: int,
    user_id: int,  # admin's User.id — plays both sides
    mode: str,
    osu_api,
    thread_id: Optional[int] = None,
) -> Optional[BskDuel]:
    """Create a test duel where the admin plays both sides (is_test=True)."""
    from services.bsk.duel_pick import _start_pick_phase
    async with get_db_session() as session:
        user = await _get_user(session, user_id)
        if not user:
            return None

        # Cancel any existing test duel for this user
        existing = (await session.execute(
            select(BskDuel).where(
                BskDuel.is_test == True,
                BskDuel.status.in_(['pending', 'accepted', 'round_active']),
                (BskDuel.player1_user_id == user_id) | (BskDuel.player2_user_id == user_id),
            )
        )).scalar_one_or_none()
        if existing:
            existing.status = 'cancelled'
            await session.commit()

        test_target_score = _target_score_for_mode(mode)
        duel = BskDuel(
            player1_user_id=user_id,
            player2_user_id=user_id,
            mode=mode,
            is_test=True,
            status='accepted',
            chat_id=chat_id,
            message_thread_id=thread_id,
            total_rounds=0,
            target_score=test_target_score,
            accepted_at=datetime.now(timezone.utc),
            version=2,
        )
        session.add(duel)
        await session.commit()
        await session.refresh(duel)

        msg = await bot.send_message(
            chat_id,
            f"🧪 <b>ТЕСТОВАЯ ДУЭЛЬ</b>\n\n"
            f"Игрок: <b>{escape_html(user.osu_username)}</b> (оба слота)\n"
            f"Режим: <b>{mode.upper()}</b> · цель {TARGET_SCORE:,} pts\n\n"
            f"<i>Выбирай карту для каждого раунда.\n"
            f"bsktestround — симулировать раунд\n"
            f"bsktestend   — завершить</i>",
            parse_mode="HTML",
            message_thread_id=thread_id,
        )
        duel.message_id = msg.message_id
        await session.commit()

        # Create IRC room for the test duel
        from services.bancho_irc import get_irc_client
        irc = get_irc_client()
        if irc.connected and user.osu_username:
            try:
                from services.bsk.irc_room import create_duel_room
                match_id = await create_duel_room(
                    irc, duel.id, user.osu_username, user.osu_username,
                    mode=mode, is_test=True,
                )
                if match_id:
                    duel.osu_match_id = str(match_id)
                    await session.commit()
            except Exception as e:
                logger.warning(f"create_test_duel: IRC room creation failed for duel {duel.id}: {e}")

    await _start_pick_phase(bot, duel.id, osu_api)
    return duel


async def simulate_test_round(
    bot: Bot,
    duel_id: int,
    p1_pp: float = 300.0,
    p1_acc: float = 97.5,
    p1_combo_ratio: float = 0.95,
    p1_misses: int = 1,
    p2_pp: float = 280.0,
    p2_acc: float = 96.0,
    p2_combo_ratio: float = 0.90,
    p2_misses: int = 2,
) -> bool:
    """Inject fake scores into the current round of a test duel."""
    from services.bsk.duel_round import _complete_round
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id, BskDuel.is_test == True)
        )).scalar_one_or_none()
        if not duel or duel.status != 'round_active':
            return False

        rnd = (await session.execute(
            select(BskDuelRound).where(
                BskDuelRound.duel_id == duel_id,
                BskDuelRound.status == 'waiting',
            ).order_by(BskDuelRound.round_number.desc())
        )).scalar_one_or_none()
        if not rnd:
            return False

        max_combo = max(int(rnd.star_rating * 200), 100)
        p1_combo = int(max_combo * p1_combo_ratio)
        p2_combo = int(max_combo * p2_combo_ratio)

        rnd.player1_pp = p1_pp
        rnd.player1_accuracy = p1_acc
        rnd.player1_combo = p1_combo
        rnd.player1_misses = p1_misses
        rnd.player1_composite = composite_score(p1_acc, p1_combo, max_combo, p1_misses)
        rnd.player1_points = composite_points(p1_acc, p1_combo, max_combo, p1_misses, mode=duel.mode)
        rnd.player1_submitted_at = datetime.now(timezone.utc)

        rnd.player2_pp = p2_pp
        rnd.player2_accuracy = p2_acc
        rnd.player2_combo = p2_combo
        rnd.player2_misses = p2_misses
        rnd.player2_composite = composite_score(p2_acc, p2_combo, max_combo, p2_misses)
        rnd.player2_points = composite_points(p2_acc, p2_combo, max_combo, p2_misses, mode=duel.mode)
        rnd.player2_submitted_at = datetime.now(timezone.utc)

        await _complete_round(bot, duel, rnd, session)
    return True

async def cancel_test_duel(bot: Bot, duel_id: int, user_id: int) -> bool:
    """Cancel a test duel immediately. Returns False if not allowed."""
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()

        if not duel or not duel.is_test:
            return False
        if duel.player1_user_id != user_id:
            return False
        if duel.status not in ('accepted', 'round_active'):
            return False

        duel.status = 'cancelled'
        duel.pick_candidates = None
        duel.pick_candidates_p1 = None
        duel.pick_candidates_p2 = None
        duel.pick_p1 = None
        duel.pick_p2 = None
        duel.pick_turn = None
        duel.pick_played = None

        # Sweep any active rounds so they don't dangle in 'waiting' forever
        now = datetime.now(timezone.utc)
        await session.execute(
            sa_update(BskDuelRound)
            .where(
                BskDuelRound.duel_id == duel_id,
                BskDuelRound.status.in_(('waiting', 'active')),
            )
            .values(status='cancelled', completed_at=now)
        )
        await session.commit()

    _pool_state.pop(duel_id, None)
    _ban_state.pop(duel_id, None)

    await safe_edit_text(
        bot,
        "❌ <b>Тестовая дуэль отменена.</b>",
        chat_id=duel.chat_id,
        message_id=duel.message_id,
        parse_mode="HTML",
    )
    return True
