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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User
from services.bsk.composite import composite_score
from services.bsk.map_selector import get_map_for_round, next_star_rating
from services.bsk.rating import update_ratings
from utils.logger import get_logger

logger = get_logger("bsk.duel_manager")

_osu_api = None
_bot: Optional[Bot] = None


def init_duel_manager(bot: Bot, osu_api) -> None:
    global _osu_api, _bot
    _bot = bot
    _osu_api = osu_api

ACCEPT_TIMEOUT_MINUTES = 5
SCORE_POLL_INTERVAL = 15  # seconds
TOTAL_ROUNDS = 5


def _forfeit_deadline(map_length_seconds: int) -> datetime:
    buffer = 5 * 60  # 5 min buffer
    return datetime.now(timezone.utc) + timedelta(seconds=map_length_seconds + buffer)


def _accept_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"bskd:accept:{duel_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bskd:decline:{duel_id}"),
    ]])


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
) -> Optional[BskDuel]:
    """Create a pending duel and send accept message to group chat."""
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
        duel = BskDuel(
            player1_user_id=challenger_id,
            player2_user_id=opponent_id,
            mode=mode,
            status='pending',
            chat_id=chat_id,
            total_rounds=TOTAL_ROUNDS,
            expires_at=expires_at,
        )
        session.add(duel)
        await session.commit()
        await session.refresh(duel)

        msg = await bot.send_message(
            chat_id,
            f"⚔️ <b>{challenger.osu_username}</b> вызывает <b>{opponent.osu_username}</b> на BSK дуэль!\n"
            f"Режим: <b>{mode.upper()}</b> · {TOTAL_ROUNDS} раундов\n\n"
            f"У <b>{opponent.osu_username}</b> есть {ACCEPT_TIMEOUT_MINUTES} минут чтобы принять.",
            parse_mode="HTML",
            reply_markup=_accept_keyboard(duel.id),
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

        if not duel or duel.status != 'pending':
            return False
        if duel.player2_user_id != user_id:
            return False

        now = datetime.now(timezone.utc)
        expires = duel.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires and now > expires:
            return False

        duel.status = 'accepted'
        duel.accepted_at = now
        await session.commit()

    await _start_next_round(bot, duel_id, osu_api)
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
        name = p2.osu_username if p2 else "Игрок"

        try:
            await bot.edit_message_text(
                f"❌ <b>{name}</b> отклонил вызов.",
                chat_id=duel.chat_id,
                message_id=duel.message_id,
                parse_mode="HTML",
            )
        except Exception:
            pass

    return True


async def _start_next_round(bot: Bot, duel_id: int, osu_api) -> None:
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status not in ('accepted', 'round_active'):
            return

        if duel.current_round >= duel.total_rounds:
            await _finish_duel(bot, duel_id)
            return

        # Get played map ids
        played = (await session.execute(
            select(BskDuelRound.beatmap_id).where(BskDuelRound.duel_id == duel_id)
        )).scalars().all()

        # Determine target SR
        if duel.current_round == 0:
            # First round: base SR from average mu_global of both players
            from db.models.bsk_rating import BskRating
            r1 = (await session.execute(
                select(BskRating).where(BskRating.user_id == duel.player1_user_id, BskRating.mode == duel.mode)
            )).scalar_one_or_none()
            r2 = (await session.execute(
                select(BskRating).where(BskRating.user_id == duel.player2_user_id, BskRating.mode == duel.mode)
            )).scalar_one_or_none()
            mu1 = r1.mu_global if r1 else 1000.0
            mu2 = r2.mu_global if r2 else 1000.0
            base_sr = round((mu1 + mu2) / 2 / 200, 1)
            base_sr = max(2.0, min(base_sr, 8.0))
            duel.current_star_rating = base_sr
        else:
            base_sr = duel.current_star_rating

        target_sr = base_sr + duel.pressure_offset
        beatmap = await get_map_for_round(target_sr, exclude_ids=list(played))

        if not beatmap:
            logger.error(f"No map found for duel {duel_id}, SR={target_sr}")
            await _finish_duel(bot, duel_id)
            return

        duel.current_round += 1
        duel.status = 'round_active'

        forfeit_at = _forfeit_deadline(beatmap.length or 180)
        round_entry = BskDuelRound(
            duel_id=duel_id,
            round_number=duel.current_round,
            beatmap_id=beatmap.beatmap_id,
            beatmapset_id=beatmap.beatmapset_id,
            beatmap_title=f"{beatmap.artist} - {beatmap.title} [{beatmap.version}]",
            star_rating=beatmap.star_rating,
            w_aim=beatmap.w_aim,
            w_speed=beatmap.w_speed,
            w_acc=beatmap.w_acc,
            w_cons=beatmap.w_cons,
            status='waiting',
            forfeit_at=forfeit_at,
        )
        session.add(round_entry)
        await session.commit()
        await session.refresh(round_entry)

        p1 = await _get_user(session, duel.player1_user_id)
        p2 = await _get_user(session, duel.player2_user_id)

        mins = (beatmap.length or 180) // 60
        secs = (beatmap.length or 180) % 60
        forfeit_mins = (beatmap.length or 180) // 60 + 5

        try:
            await bot.edit_message_text(
                f"🎵 <b>Раунд {duel.current_round}/{duel.total_rounds}</b>\n\n"
                f"<b>{beatmap.artist} - {beatmap.title}</b> [{beatmap.version}]\n"
                f"⭐ {beatmap.star_rating:.2f}  ·  🕐 {mins}:{secs:02d}  ·  {beatmap.bpm:.0f} BPM\n\n"
                f"<b>{p1.osu_username}</b> vs <b>{p2.osu_username}</b>\n"
                f"Идите играть! У вас {forfeit_mins} минут.",
                chat_id=duel.chat_id,
                message_id=duel.message_id,
                parse_mode="HTML",
            )
        except Exception:
            pass

    asyncio.create_task(_monitor_round(bot, duel_id, round_entry.id, osu_api))


async def _monitor_round(bot: Bot, duel_id: int, round_id: int, osu_api) -> None:
    """Poll recent scores for both players until both submit or forfeit."""
    while True:
        await asyncio.sleep(SCORE_POLL_INTERVAL)

        async with get_db_session() as session:
            rnd = (await session.execute(
                select(BskDuelRound).where(BskDuelRound.id == round_id)
            )).scalar_one_or_none()
            if not rnd or rnd.status != 'waiting':
                return

            duel = (await session.execute(
                select(BskDuel).where(BskDuel.id == duel_id)
            )).scalar_one_or_none()
            if not duel:
                return

            now = datetime.now(timezone.utc)
            forfeit_at = rnd.forfeit_at
            if forfeit_at and forfeit_at.tzinfo is None:
                forfeit_at = forfeit_at.replace(tzinfo=timezone.utc)

            # Check forfeit
            if forfeit_at and now > forfeit_at:
                await _handle_forfeit(bot, duel, rnd, session)
                await session.commit()
                return

            p1 = await _get_user(session, duel.player1_user_id)
            p2 = await _get_user(session, duel.player2_user_id)

            # Check scores for each player
            for player_num, user in [(1, p1), (2, p2)]:
                already = getattr(rnd, f'player{player_num}_composite')
                if already is not None:
                    continue
                if not user or not user.osu_user_id:
                    continue

                from services.oauth.token_manager import get_valid_token
                token = await get_valid_token(user.id)
                if not token:
                    continue

                score = await _find_score_on_map(osu_api, token, user.osu_user_id, rnd.beatmap_id, rnd.started_at)
                if not score:
                    continue

                pp = float(score.get('pp') or 0)
                acc = float(score.get('accuracy') or 0) * 100
                combo = int(score.get('max_combo') or 0)
                misses = int((score.get('statistics') or {}).get('miss') or 0)
                max_combo = int((score.get('beatmap') or {}).get('max_combo') or combo or 1)
                comp = composite_score(pp, acc, combo, max_combo, misses)

                setattr(rnd, f'player{player_num}_score', int(score.get('score') or 0))
                setattr(rnd, f'player{player_num}_accuracy', acc)
                setattr(rnd, f'player{player_num}_combo', combo)
                setattr(rnd, f'player{player_num}_misses', misses)
                setattr(rnd, f'player{player_num}_pp', pp)
                setattr(rnd, f'player{player_num}_composite', comp)
                setattr(rnd, f'player{player_num}_submitted_at', now)

            # Both submitted?
            if rnd.player1_composite is not None and rnd.player2_composite is not None:
                await _complete_round(bot, duel, rnd, session)
                await session.commit()
                return

            await session.commit()


async def _find_score_on_map(osu_api, token: str, osu_user_id: int, beatmap_id: int, after: datetime):
    """Check recent scores for a score on beatmap_id submitted after `after`."""
    import aiohttp
    url = f"https://osu.ppy.sh/api/v2/users/{osu_user_id}/scores/recent"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 10, "include_fails": 0, "mode": "osu"}

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return None
                scores = await resp.json()
    except Exception:
        return None

    for sc in scores:
        if int((sc.get('beatmap') or {}).get('id') or 0) != beatmap_id:
            continue
        created_at = sc.get('created_at') or sc.get('ended_at')
        if not created_at:
            continue
        try:
            sc_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            if sc_time.tzinfo is None:
                sc_time = sc_time.replace(tzinfo=timezone.utc)
            after_tz = after if after.tzinfo else after.replace(tzinfo=timezone.utc)
            if sc_time >= after_tz:
                return sc
        except Exception:
            continue
    return None


async def _complete_round(bot: Bot, duel: BskDuel, rnd: BskDuelRound, session) -> None:
    c1 = rnd.player1_composite or 0
    c2 = rnd.player2_composite or 0
    winner = 1 if c1 >= c2 else 2

    rnd.winner_player = winner
    rnd.status = 'completed'
    rnd.completed_at = datetime.now(timezone.utc)

    duel.player1_total_score += c1
    duel.player2_total_score += c2

    # Adaptive pressure
    new_sr = next_star_rating(
        duel.current_star_rating,
        winner,
        duel.player1_total_score,
        duel.player2_total_score,
        duel.current_star_rating,
    )
    duel.pressure_offset = new_sr - duel.current_star_rating

    p1 = await _get_user(session, duel.player1_user_id)
    p2 = await _get_user(session, duel.player2_user_id)
    winner_name = p1.osu_username if winner == 1 else p2.osu_username

    try:
        await bot.edit_message_text(
            f"✅ <b>Раунд {rnd.round_number} завершён!</b>\n\n"
            f"<b>{p1.osu_username}</b>: {c1:.3f}\n"
            f"<b>{p2.osu_username}</b>: {c2:.3f}\n\n"
            f"Победитель раунда: <b>{winner_name}</b>\n"
            f"Счёт: {duel.player1_total_score:.3f} — {duel.player2_total_score:.3f}",
            chat_id=duel.chat_id,
            message_id=duel.message_id,
            parse_mode="HTML",
        )
    except Exception:
        pass

    asyncio.create_task(_next_round_delayed(bot, duel.id, 5))


async def _handle_forfeit(bot: Bot, duel: BskDuel, rnd: BskDuelRound, session) -> None:
    p1_done = rnd.player1_composite is not None
    p2_done = rnd.player2_composite is not None

    if p1_done and not p2_done:
        rnd.winner_player = 1
        rnd.player2_composite = 0.0
    elif p2_done and not p1_done:
        rnd.winner_player = 2
        rnd.player1_composite = 0.0
    else:
        rnd.winner_player = None

    rnd.status = 'forfeit'
    rnd.completed_at = datetime.now(timezone.utc)

    p1 = await _get_user(session, duel.player1_user_id)
    p2 = await _get_user(session, duel.player2_user_id)

    if rnd.winner_player:
        winner_name = p1.osu_username if rnd.winner_player == 1 else p2.osu_username
        msg = f"⏰ Время вышло! Раунд засчитан <b>{winner_name}</b> по forfeit."
    else:
        msg = "⏰ Время вышло! Оба игрока не сыграли — раунд аннулирован."

    duel.player1_total_score += rnd.player1_composite or 0
    duel.player2_total_score += rnd.player2_composite or 0

    try:
        await bot.edit_message_text(msg, chat_id=duel.chat_id, message_id=duel.message_id, parse_mode="HTML")
    except Exception:
        pass

    asyncio.create_task(_next_round_delayed(bot, duel.id, 5))


async def _next_round_delayed(bot: Bot, duel_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    await _start_next_round(bot, duel_id, _osu_api)


async def _finish_duel(bot: Bot, duel_id: int) -> None:
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            return

        p1 = await _get_user(session, duel.player1_user_id)
        p2 = await _get_user(session, duel.player2_user_id)

        s1 = duel.player1_total_score
        s2 = duel.player2_total_score

        if s1 > s2:
            winner_id = duel.player1_user_id
            loser_id = duel.player2_user_id
            winner_name = p1.osu_username
        elif s2 > s1:
            winner_id = duel.player2_user_id
            loser_id = duel.player1_user_id
            winner_name = p2.osu_username
        else:
            winner_id = None
            winner_name = "Ничья"

        duel.status = 'completed'
        duel.completed_at = datetime.now(timezone.utc)
        duel.winner_user_id = winner_id
        await session.commit()

    if winner_id and not duel.is_test:
        await update_ratings(winner_id, loser_id, duel.mode)

    try:
        test_tag = " [ТЕСТ]" if duel.is_test else ""
        await bot.edit_message_text(
            f"🏆 <b>Дуэль завершена!{test_tag}</b>\n\n"
            f"<b>{p1.osu_username}</b>: {s1:.3f}\n"
            f"<b>{p2.osu_username}</b>: {s2:.3f}\n\n"
            f"Победитель: <b>{winner_name}</b>"
            + ("\n\n<i>Тестовая дуэль — рейтинг не изменён.</i>" if duel.is_test else ""),
            chat_id=duel.chat_id,
            message_id=duel.message_id,
            parse_mode="HTML",
        )
    except Exception:
        pass


async def _expire_duel(bot: Bot, duel_id: int, osu_api) -> None:
    await asyncio.sleep(ACCEPT_TIMEOUT_MINUTES * 60)
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'pending':
            return
        duel.status = 'expired'
        await session.commit()

        try:
            await bot.edit_message_text(
                "⏰ Вызов истёк — соперник не ответил.",
                chat_id=duel.chat_id,
                message_id=duel.message_id,
                parse_mode="HTML",
            )
        except Exception:
            pass


async def create_test_duel(
    bot: Bot,
    chat_id: int,
    user_id: int,  # admin's User.id — plays both sides
    mode: str,
    osu_api,
) -> Optional[BskDuel]:
    """Create a test duel where the admin plays both sides (is_test=True)."""
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

        duel = BskDuel(
            player1_user_id=user_id,
            player2_user_id=user_id,
            mode=mode,
            is_test=True,
            status='accepted',
            chat_id=chat_id,
            total_rounds=TOTAL_ROUNDS,
            accepted_at=datetime.now(timezone.utc),
        )
        session.add(duel)
        await session.commit()
        await session.refresh(duel)

        msg = await bot.send_message(
            chat_id,
            f"🧪 <b>ТЕСТОВАЯ ДУЭЛЬ</b>\n\n"
            f"Игрок: <b>{user.osu_username}</b> (оба слота)\n"
            f"Режим: <b>{mode.upper()}</b> · {TOTAL_ROUNDS} раундов\n\n"
            f"Используй <code>bsktestround</code> для симуляции раунда.\n"
            f"Используй <code>bsktestend</code> для завершения.",
            parse_mode="HTML",
        )
        duel.message_id = msg.message_id
        await session.commit()

    await _start_next_round(bot, duel.id, osu_api)
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
        rnd.player1_composite = composite_score(p1_pp, p1_acc, p1_combo, max_combo, p1_misses)
        rnd.player1_submitted_at = datetime.now(timezone.utc)

        rnd.player2_pp = p2_pp
        rnd.player2_accuracy = p2_acc
        rnd.player2_combo = p2_combo
        rnd.player2_misses = p2_misses
        rnd.player2_composite = composite_score(p2_pp, p2_acc, p2_combo, max_combo, p2_misses)
        rnd.player2_submitted_at = datetime.now(timezone.utc)

        await _complete_round(bot, duel, rnd, session)
        await session.commit()
    return True
