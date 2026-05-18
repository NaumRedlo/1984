"""
BSK Duel Manager — orchestrates duel lifecycle:
- Create duel, accept/decline
- Round management with adaptive pressure
- Score monitoring via recent scores API
- Forfeit handling
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, update as sa_update

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User
from services.bsk.composite import composite_score, composite_points, points_multiplier_for
from services.bsk.ml_inference import predict_round_winner
from services.bsk.map_selector import (
    get_map_for_round, get_pick_candidates, next_star_rating,
    get_balanced_pick_candidates,
)
from services.bsk.rating import update_ratings
from services.bsk.duel_constants import (
    ACCEPT_TIMEOUT_MINUTES,
    BAN_TIMEOUT_SECONDS,
    CASUAL_MULTIPLIER_CAP,
    CASUAL_MULTIPLIER_INC,
    CASUAL_MULTIPLIER_STEP,
    MAX_BANS,
    MAX_ROUNDS_CASUAL,
    MAX_ROUNDS_RANKED,
    PICK_TIMEOUT_SECONDS,
    POOL_SIZE,
    RANKED_BAN_PHASE_ROUNDS,
    RANKED_MULTIPLIER_CAP,
    RANKED_MULTIPLIER_INC,
    RANKED_MULTIPLIER_STEP,
    RANKED_TARGET_SR_OFFSET,
    SCORE_POLL_INTERVAL,
    TARGET_SCORE,
    TARGET_SCORE_RANKED,
    _base_sr_for_duel,
    _forfeit_deadline,
    _max_rounds_for,
    _round_multiplier_for,
    _target_score_for_mode,
)


from services.bsk.duel_state import (
    ban_state as _ban_state,
    pool_state as _pool_state,
)
from services.bsk.duel_telegram import send_or_edit_photo as _send_or_edit_photo
from services.bsk.duel_ui import (
    accept_keyboard as _accept_keyboard,
    ban_keyboard as _ban_keyboard,
    beatmap_links as _beatmap_links,
    format_pick_pool_links as _format_pick_pool_links,
    grid_cols_for as _grid_cols_for,
    pick_keyboard as _pick_keyboard,
)
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.telegram_safe import (
    safe_edit_caption,
    safe_edit_reply_markup,
    safe_edit_text,
)

logger = get_logger("bsk.duel_manager")

_osu_api = None
_bot: Optional[Bot] = None


def init_duel_manager(bot: Bot, osu_api) -> None:
    global _osu_api, _bot
    _bot = bot
    _osu_api = osu_api


async def _get_user(session, user_id: int) -> Optional[User]:
    return (await session.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()


async def create_duel(
    bot: Bot,
    chat_id: int,
    challenger_id: int,  # User.id
    opponent_id: int,    # User.id
    mode: str,
    osu_api,
    thread_id: Optional[int] = None,
) -> Optional[BskDuel]:
    """Create a pending duel and send accept message to group chat.

    `thread_id` (Telegram forum topic message_thread_id) — when non-None, ALL
    public messages for this duel (challenge, round cards, finish) will be
    posted into that topic instead of the chat's General. Stored on the duel
    so post-restart recovery uses the same topic.
    """
    async with get_db_session() as session:
        challenger = await _get_user(session, challenger_id)
        opponent = await _get_user(session, opponent_id)
        if not challenger or not opponent:
            return None

        # Check no active duel between them
        active = (await session.execute(
            select(BskDuel).where(
                BskDuel.status.in_(['pending', 'accepted', 'round_active']),
                (
                    (BskDuel.player1_user_id == challenger_id) |
                    (BskDuel.player2_user_id == challenger_id) |
                    (BskDuel.player1_user_id == opponent_id) |
                    (BskDuel.player2_user_id == opponent_id)
                )
            )
        )).scalar_one_or_none()
        if active:
            return None

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCEPT_TIMEOUT_MINUTES)
        duel_target_score = _target_score_for_mode(mode)
        duel = BskDuel(
            player1_user_id=challenger_id,
            player2_user_id=opponent_id,
            mode=mode,
            status='pending',
            chat_id=chat_id,
            message_thread_id=thread_id,
            total_rounds=0,
            target_score=duel_target_score,
            expires_at=expires_at,
            version=2,
        )
        session.add(duel)
        await session.commit()
        await session.refresh(duel)

        challenger_name = escape_html(challenger.osu_username)
        opponent_name = escape_html(opponent.osu_username)

        # Build a real Telegram mention for the opponent so they get pinged
        # in the group chat. Falls back to plain italics if telegram_id is
        # somehow missing (legacy / placeholder accounts).
        if opponent.telegram_id:
            opponent_mention = (
                f'<a href="tg://user?id={opponent.telegram_id}">'
                f'{opponent_name}</a>'
            )
        else:
            opponent_mention = f"<i>{opponent_name}</i>"

        msg = await bot.send_message(
            chat_id,
            f"⚔️ <b>ВЫЗОВ НА ДУЭЛЬ</b>\n\n"
            f"<b>{challenger_name}</b> бросает вызов <b>{opponent_name}</b>!\n\n"
            f"🎮 Режим: <b>{mode.upper()}</b>\n"
            f"🏁 Цель: <b>{duel_target_score:,} pts</b>\n"
            f"⏳ Время на принятие: <b>{ACCEPT_TIMEOUT_MINUTES} мин</b>\n\n"
            f"{opponent_mention}, принимаешь вызов?",
            parse_mode="HTML",
            reply_markup=_accept_keyboard(duel.id),
            message_thread_id=thread_id,
        )

        duel.message_id = msg.message_id
        await session.commit()

    # Schedule expiry check
    asyncio.create_task(_expire_duel(bot, duel.id, osu_api))
    return duel


async def accept_duel(bot: Bot, duel_id: int, user_id: int, osu_api) -> bool:
    """Accept a pending duel. Returns False if not allowed."""
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()

        if not duel or duel.player2_user_id != user_id:
            return False

        now = datetime.now(timezone.utc)
        expires = duel.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires and now > expires:
            return False

        # Atomic CAS: only accept if still pending (prevents double-accept race)
        result = await session.execute(
            sa_update(BskDuel)
            .where(BskDuel.id == duel_id, BskDuel.status == 'pending')
            .values(status='accepted', accepted_at=now)
        )
        if result.rowcount == 0:
            return False

        # Guarantee BskRating rows exist for both players seeded from their pp,
        # so the first round's update_ratings doesn't create them with mu=0.
        from services.bsk.rating import get_or_create_rating
        p1 = await _get_user(session, duel.player1_user_id)
        p2 = await _get_user(session, duel.player2_user_id)
        await session.commit()

        try:
            await get_or_create_rating(
                duel.player1_user_id, duel.mode,
                player_pp=float(p1.player_pp or 0) if p1 else 0.0,
            )
            await get_or_create_rating(
                duel.player2_user_id, duel.mode,
                player_pp=float(p2.player_pp or 0) if p2 else 0.0,
            )
        except Exception as e:
            logger.error(f"accept_duel: get_or_create_rating failed for duel {duel_id}: {e}", exc_info=True)

        # Create IRC room if connected
        from services.bancho_irc import get_irc_client
        irc = get_irc_client()
        if irc.connected and p1 and p2 and p1.osu_username and p2.osu_username:
            try:
                from services.bsk.irc_room import create_duel_room
                match_id = await create_duel_room(irc, duel_id, p1.osu_username, p2.osu_username, mode=duel.mode, is_test=duel.is_test)
                if match_id:
                    async with get_db_session() as _irc_sess:
                        _d = (await _irc_sess.execute(
                            select(BskDuel).where(BskDuel.id == duel_id)
                        )).scalar_one_or_none()
                        if _d:
                            _d.osu_match_id = str(match_id)
                            await _irc_sess.commit()
            except Exception as e:
                logger.warning(f"accept_duel: IRC room creation failed for duel {duel_id}: {e}")

    await _start_pick_phase(bot, duel_id, osu_api)
    return True


async def decline_duel(bot: Bot, duel_id: int, user_id: int) -> bool:
    """Decline a pending duel."""
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()

        if not duel or duel.status != 'pending':
            return False
        if duel.player2_user_id != user_id:
            return False

        duel.status = 'cancelled'
        await session.commit()

        p2 = await _get_user(session, duel.player2_user_id)
        name = escape_html(p2.osu_username) if p2 else "Игрок"

        await safe_edit_text(
            bot,
            f"❌ <b>{name}</b> отклонил вызов.\n\n"
            f"<i>Дуэль отменена.</i>",
            chat_id=duel.chat_id,
            message_id=duel.message_id,
            parse_mode="HTML",
        )

    return True



async def vote_pause(bot: Bot, duel_id: int, user_id: int) -> str:
    """
    Register a pause vote from user_id. Returns:
    - 'voted'   — vote registered, waiting for second player
    - 'paused'  — both voted, round paused (forfeit_at extended)
    - 'already' — already voted
    - 'invalid' — duel not found or not round_active
    """
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'round_active':
            return 'invalid'

        if user_id == duel.player1_user_id:
            bit = 1
        elif user_id == duel.player2_user_id:
            bit = 2
        else:
            return 'invalid'

        # Block stacking pauses: must resume_duel before voting for another pause
        if duel.paused_at is not None:
            return 'already'

        # Test duel: single vote is enough to pause
        if duel.is_test:
            rnd = (await session.execute(
                select(BskDuelRound).where(
                    BskDuelRound.duel_id == duel_id,
                    BskDuelRound.status == 'waiting',
                ).order_by(BskDuelRound.round_number.desc())
            )).scalar_one_or_none()
            if rnd and rnd.forfeit_at:
                forfeit_at = rnd.forfeit_at
                if forfeit_at.tzinfo is None:
                    forfeit_at = forfeit_at.replace(tzinfo=timezone.utc)
                rnd.forfeit_at = forfeit_at + timedelta(minutes=15)
            duel.paused_at = datetime.now(timezone.utc)
            await session.commit()
            try:
                await bot.send_message(
                    duel.chat_id,
                    "⏸ <b>Дуэль приостановлена</b>\n\nВремя форфейта продлено на <b>15 минут</b>.",
                    parse_mode="HTML",
                    message_thread_id=duel.message_thread_id,
                )
            except Exception:
                logger.debug(f"vote_pause: pause notice send failed for duel {duel_id}", exc_info=True)
            return 'paused'

        # Atomic CAS: set bit only if not already set
        result = await session.execute(
            sa_update(BskDuel)
            .where(
                BskDuel.id == duel_id,
                BskDuel.pause_votes.op('&')(bit) == 0,
            )
            .values(pause_votes=BskDuel.pause_votes.op('|')(bit))
        )
        if result.rowcount == 0:
            return 'already'
        await session.refresh(duel)

        if duel.pause_votes == 3:  # both voted
            # Extend forfeit_at on current active round by 15 min
            rnd = (await session.execute(
                select(BskDuelRound).where(
                    BskDuelRound.duel_id == duel_id,
                    BskDuelRound.status == 'waiting',
                ).order_by(BskDuelRound.round_number.desc())
            )).scalar_one_or_none()
            if rnd and rnd.forfeit_at:
                forfeit_at = rnd.forfeit_at
                if forfeit_at.tzinfo is None:
                    forfeit_at = forfeit_at.replace(tzinfo=timezone.utc)
                rnd.forfeit_at = forfeit_at + timedelta(minutes=15)
            duel.pause_votes = 0
            duel.paused_at = datetime.now(timezone.utc)
            await session.commit()

            try:
                await bot.send_message(
                    duel.chat_id,
                    "⏸ <b>Дуэль приостановлена</b>\n\n"
                    "Оба игрока проголосовали за паузу.\n"
                    "Время форфейта продлено на <b>15 минут</b>.",
                    parse_mode="HTML",
                    message_thread_id=duel.message_thread_id,
                )
            except Exception:
                logger.debug(f"vote_pause: both-voted notice send failed for duel {duel_id}", exc_info=True)
            return 'paused'

        await session.commit()
        return 'voted'


async def resume_duel(bot: Bot, duel_id: int, user_id: int) -> str:
    """
    Resume a paused duel. Returns:
    - 'resumed' — success
    - 'invalid' — duel not found, not paused, or user not a participant
    """
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'round_active':
            return 'invalid'
        if duel.paused_at is None:
            return 'invalid'
        if user_id not in (duel.player1_user_id, duel.player2_user_id):
            return 'invalid'

        duel.paused_at = None
        chat_id = duel.chat_id
        thread_id = duel.message_thread_id
        await session.commit()

    try:
        await bot.send_message(
            chat_id,
            "▶️ <b>Дуэль возобновлена!</b>",
            parse_mode="HTML",
            message_thread_id=thread_id,
        )
    except Exception:
        logger.debug(f"resume_duel: resume notice send failed for duel {duel_id}", exc_info=True)
    return 'resumed'


async def cancel_duel(bot: Bot, duel_id: int, user_id: int) -> str:
    """
    Cancel any active duel that user_id is part of.
    Returns:
      'cancelled'       — done
      'not_found'       — no such active duel
      'not_participant' — user is not in this duel
      'not_challenger'  — pending duel can only be cancelled by challenger
    """
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()

        if not duel or duel.status not in ('pending', 'accepted', 'round_active'):
            return 'not_found'
        if user_id not in (duel.player1_user_id, duel.player2_user_id):
            return 'not_participant'
        if duel.status == 'pending' and duel.player1_user_id != user_id:
            return 'not_challenger'

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

    cancel_text = (
        "❌ Вызов отменён инициатором."
        if duel.player1_user_id == user_id
        else "❌ <b>Дуэль отменена соперником.</b>"
    )
    await safe_edit_text(
        bot,
        cancel_text,
        chat_id=duel.chat_id,
        message_id=duel.message_id,
        parse_mode="HTML",
    )
    return 'cancelled'


# ── Re-exports so existing callers don't break ──────────────────────────────
from services.bsk.duel_pick import (  # noqa: E402
    _start_pick_phase,
    toggle_ban,
    confirm_ban,
    submit_pick,
)
from services.bsk.duel_round import (  # noqa: E402
    _safe_monitor_round,
    _start_next_round,
    _complete_round,
    _post_round_routing,
)
from services.bsk.duel_finish import (  # noqa: E402
    _finish_duel,
    _expire_duel,
)
from services.bsk.duel_test import (  # noqa: E402
    create_test_duel,
    simulate_test_round,
    cancel_test_duel,
)
from services.bsk.duel_recover import recover_active_duels  # noqa: E402
