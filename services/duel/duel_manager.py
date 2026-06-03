"""Duel manager — duel lifecycle: create, accept (build pool + IRC room +
launch the round engine), decline, cancel.  Rating math lives in
``services.duel.rating``; the round loop lives in ``services.duel.round_engine``.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from sqlalchemy import select, update as sa_update

from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_round import DuelRound
from db.models.user import User
from services.duel.duel_constants import (
    ACCEPT_TIMEOUT_MINUTES, pool_size_for, win_target_for, DUEL_POOL_MAPS,
)
from services.duel.map_selector import get_pick_candidates
from services.duel.rating import get_or_create_rating, rating_to_sr
from services.duel import round_engine
from services.duel.duel_recover import recover_active_duels  # noqa: F401 — re-export
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.telegram_safe import safe_edit_text

logger = get_logger("duel.manager")

_osu_api = None
_bot: Optional[Bot] = None


def init_duel_manager(bot: Bot, osu_api) -> None:
    global _osu_api, _bot
    _bot = bot
    _osu_api = osu_api


def _accept_keyboard(duel_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"dueld:accept:{duel_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dueld:decline:{duel_id}"),
    ]])


async def _get_user(session, user_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


# ── create ───────────────────────────────────────────────────────────────────
async def create_duel(
    bot: Bot,
    chat_id: int,
    challenger_id: int,
    opponent_id: int,
    mode: str,
    osu_api,
    thread_id: Optional[int] = None,
) -> Optional[Duel]:
    """Create a pending duel and post the accept message. ``thread_id`` pins all
    of this duel's public messages to a forum topic (stored for recovery)."""
    async with get_db_session() as session:
        challenger = await _get_user(session, challenger_id)
        opponent = await _get_user(session, opponent_id)
        if not challenger or not opponent:
            return None

        active = (await session.execute(
            select(Duel).where(
                Duel.status.in_(['pending', 'accepted', 'round_active']),
                (
                    (Duel.player1_user_id == challenger_id) |
                    (Duel.player2_user_id == challenger_id) |
                    (Duel.player1_user_id == opponent_id) |
                    (Duel.player2_user_id == opponent_id)
                )
            )
        )).scalar_one_or_none()
        if active:
            return None

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCEPT_TIMEOUT_MINUTES)
        duel = Duel(
            player1_user_id=challenger_id,
            player2_user_id=opponent_id,
            mode=mode,
            status='pending',
            chat_id=chat_id,
            message_thread_id=thread_id,
            total_rounds=pool_size_for(mode),
            win_target=win_target_for(mode),
            expires_at=expires_at,
        )
        session.add(duel)
        await session.commit()
        await session.refresh(duel)

        challenger_name = escape_html(challenger.osu_username)
        opponent_name = escape_html(opponent.osu_username)
        if opponent.telegram_id:
            opponent_mention = f'<a href="tg://user?id={opponent.telegram_id}">{opponent_name}</a>'
        else:
            opponent_mention = f"<i>{opponent_name}</i>"

        msg = await bot.send_message(
            chat_id,
            f"⚔️ <b>ВЫЗОВ НА ДУЭЛЬ</b>\n\n"
            f"<b>{challenger_name}</b> бросает вызов <b>{opponent_name}</b>!\n\n"
            f"🎮 Режим: <b>{mode.upper()}</b>\n"
            f"🏁 Формат: <b>Bo{pool_size_for(mode)}</b> (до {win_target_for(mode)} побед)\n"
            f"⏳ Время на принятие: <b>{ACCEPT_TIMEOUT_MINUTES} мин</b>\n\n"
            f"{opponent_mention}, принимаешь вызов?",
            parse_mode="HTML",
            reply_markup=_accept_keyboard(duel.id),
            message_thread_id=thread_id,
        )
        duel.message_id = msg.message_id
        await session.commit()

    asyncio.create_task(_expire_duel(bot, duel.id))
    return duel


# ── accept / decline ─────────────────────────────────────────────────────────
async def accept_duel(bot: Bot, duel_id: int, user_id: int, osu_api) -> bool:
    """Accept a pending duel: seed ratings, build the level-matched pool, open
    the IRC room, then hand off to the round engine. IRC is required."""
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.player2_user_id != user_id:
            return False

        now = datetime.now(timezone.utc)
        expires = duel.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires and now > expires:
            return False

        cas = await session.execute(
            sa_update(Duel)
            .where(Duel.id == duel_id, Duel.status == 'pending')
            .values(status='accepted', accepted_at=now)
        )
        if cas.rowcount == 0:
            return False
        p1 = await _get_user(session, duel.player1_user_id)
        p2 = await _get_user(session, duel.player2_user_id)
        mode = duel.mode
        chat_id, thread_id, message_id = duel.chat_id, duel.message_thread_id, duel.message_id
        await session.commit()

    # Seed ratings from pp so the pool targets the right level.
    r1 = await get_or_create_rating(duel.player1_user_id, mode, float(p1.player_pp or 0) if p1 else 0.0)
    r2 = await get_or_create_rating(duel.player2_user_id, mode, float(p2.player_pp or 0) if p2 else 0.0)
    target_sr = rating_to_sr((r1.mu + r2.mu) / 2.0)

    # Each player gets their OWN pool of DUEL_POOL_MAPS maps: both are built
    # around the shared average SR, but the map sets are distinct (pool_b
    # excludes pool_a's ids). The weaker player (lower conservative, μ as
    # tie-break) serves first; rounds then alternate pools — we precompute the
    # interleaved play order [weak0, strong0, weak1, strong1, …] and store it in
    # pool_beatmap_ids, so the round engine just walks it unchanged.
    pool_a = await get_pick_candidates(target_sr, n=DUEL_POOL_MAPS)
    pool_b = await get_pick_candidates(
        target_sr, n=DUEL_POOL_MAPS,
        exclude_ids=[m.beatmap_id for m in pool_a],
    )
    if not pool_a or not pool_b:
        await _abort(bot, duel_id, chat_id, message_id,
                     "❌ В пуле недостаточно карт для двух наборов — дуэль отменена.")
        return True

    # Assign pools: pool_a → weaker player, pool_b → stronger.
    weaker_is_p1 = (r1.conservative, r1.mu) <= (r2.conservative, r2.mu)
    weak_pool, strong_pool = pool_a, pool_b
    p1_pool, p2_pool = (weak_pool, strong_pool) if weaker_is_p1 else (strong_pool, weak_pool)

    # Interleave starting with the weaker player's pool (handles unequal sizes).
    interleaved = []
    for i in range(max(len(weak_pool), len(strong_pool))):
        if i < len(weak_pool):
            interleaved.append(weak_pool[i])
        if i < len(strong_pool):
            interleaved.append(strong_pool[i])
    pool_ids = [m.beatmap_id for m in interleaved]

    # Snapshot each player's own pool for their DM card, while the rows are fresh.
    def _snapshot(pool_rows):
        return [
            {
                "artist": m.artist, "title": m.title, "version": m.version,
                "creator": m.creator,
                "star_rating": m.star_rating, "length": m.length, "bpm": m.bpm,
                "max_combo": m.max_combo, "beatmapset_id": m.beatmapset_id,
                "cs": m.cs, "ar": m.ar, "od": m.od, "hp_drain": m.hp_drain,
            }
            for m in pool_rows
        ]
    p1_maps = _snapshot(p1_pool)
    p2_maps = _snapshot(p2_pool)

    # IRC is mandatory.
    from services.bancho_irc import get_irc_client
    irc = get_irc_client()
    if not irc.connected or not (p1 and p2 and p1.osu_username and p2.osu_username):
        await _abort(bot, duel_id, chat_id, message_id,
                     "❌ IRC недоступен — дуэль отменена. Попробуйте позже.")
        return True

    match_id = None
    try:
        from services.duel.irc_room import create_duel_room
        match_id = await create_duel_room(irc, duel_id, p1.osu_username, p2.osu_username, mode=mode)
    except Exception as e:
        logger.warning(f"accept_duel: IRC room creation failed for duel {duel_id}: {e}")
    if not match_id:
        await _abort(bot, duel_id, chat_id, message_id,
                     "❌ Не удалось создать IRC-комнату — дуэль отменена.")
        return True

    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one()
        duel.osu_match_id = int(match_id)
        duel.pool_beatmap_ids = ",".join(str(i) for i in pool_ids)
        duel.status = 'round_active'
        await session.commit()

    try:
        await bot.send_message(
            chat_id,
            f"✅ Вызов принят! Комната создана. У каждого свой набор из "
            f"<b>{DUEL_POOL_MAPS}</b> карт под средний уровень (~{target_sr:.1f}★); "
            f"карты раундов чередуются.\nПул отправлен вам в личку — "
            f"принимайте инвайт в osu! и играйте.",
            parse_mode="HTML", message_thread_id=thread_id,
        )
    except Exception:
        logger.debug(f"accept_duel: start notice failed for duel {duel_id}", exc_info=True)

    # DM each player their OWN map pool (distinct maps, same target SR).
    try:
        from aiogram.types import BufferedInputFile
        from services.image import card_renderer

        async def _send_pool(u, player_maps):
            if not u or not player_maps:
                return
            pool_data = {
                "mode": mode,
                "total_rounds": pool_size_for(mode),
                "win_target": win_target_for(mode),
                "target_sr": target_sr,
                "maps": player_maps,
            }
            try:
                png = (await card_renderer.generate_duel_pool_card_async(pool_data)).getvalue()
                await bot.send_photo(
                    u.telegram_id,
                    BufferedInputFile(png, filename="duel_pool.png"),
                    caption=(
                        "🎴 Твой пул — 6 карт под средний уровень обоих игроков. "
                        "У соперника свой набор; карты раундов чередуются "
                        "(кто слабее по рейтингу — играет первым)."
                    ),
                )
            except Exception:
                logger.debug(f"accept_duel: pool DM to {u.telegram_id} failed", exc_info=True)

        await asyncio.gather(_send_pool(p1, p1_maps), _send_pool(p2, p2_maps))
    except Exception:
        logger.debug(f"accept_duel: pool card build failed for duel {duel_id}", exc_info=True)

    # Post the live scoreboard card; the round engine edits it in place.
    try:
        from services.duel import status_card
        await status_card.post_or_update(bot, duel_id)
    except Exception:
        logger.debug(f"accept_duel: status card post failed for duel {duel_id}", exc_info=True)

    round_engine.launch(bot, osu_api, duel_id)
    return True


async def decline_duel(bot: Bot, duel_id: int, user_id: int) -> bool:
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status != 'pending' or duel.player2_user_id != user_id:
            return False
        duel.status = 'cancelled'
        await session.commit()
        p2 = await _get_user(session, duel.player2_user_id)
        name = escape_html(p2.osu_username) if p2 else "Игрок"
        await safe_edit_text(
            bot, f"❌ <b>{name}</b> отклонил вызов.\n\n<i>Дуэль отменена.</i>",
            chat_id=duel.chat_id, message_id=duel.message_id, parse_mode="HTML",
        )
    return True


# ── cancel ───────────────────────────────────────────────────────────────────
async def cancel_duel(bot: Bot, duel_id: int, user_id: int) -> str:
    """Cancel a duel the user participates in.
    Returns 'cancelled' | 'not_found' | 'not_participant'."""
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status not in ('pending', 'accepted', 'round_active'):
            return 'not_found'
        if user_id not in (duel.player1_user_id, duel.player2_user_id):
            return 'not_participant'

        duel.status = 'cancelled'
        now = datetime.now(timezone.utc)
        await session.execute(
            sa_update(DuelRound)
            .where(DuelRound.duel_id == duel_id, DuelRound.status.in_(('waiting', 'playing')))
            .values(status='cancelled', completed_at=now)
        )
        match_id = duel.osu_match_id
        chat_id, message_id = duel.chat_id, duel.message_id
        is_challenger = duel.player1_user_id == user_id
        await session.commit()

    if match_id:
        from services.bancho_irc import get_irc_client
        from services.duel.irc_room import close_room
        irc = get_irc_client()
        if irc.connected:
            try:
                await close_room(irc, int(match_id))
            except Exception as e:
                logger.warning(f"cancel_duel: close IRC room {match_id} failed: {e}")

    text = ("❌ Вызов отменён инициатором." if is_challenger
            else "❌ <b>Дуэль отменена соперником.</b>")
    await safe_edit_text(bot, text, chat_id=chat_id, message_id=message_id, parse_mode="HTML")
    return 'cancelled'


# ── internals ────────────────────────────────────────────────────────────────
async def _abort(bot, duel_id, chat_id, message_id, text: str) -> None:
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if duel and duel.status in ('accepted', 'pending'):
            duel.status = 'cancelled'
            await session.commit()
    try:
        await safe_edit_text(bot, text, chat_id=chat_id, message_id=message_id, parse_mode="HTML")
    except Exception:
        logger.debug(f"_abort: edit failed for duel {duel_id}", exc_info=True)


async def _expire_duel(bot: Bot, duel_id: int) -> None:
    """Expire a still-pending duel after the accept window."""
    await asyncio.sleep(ACCEPT_TIMEOUT_MINUTES * 60 + 5)
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status != 'pending':
            return
        duel.status = 'expired'
        chat_id, message_id = duel.chat_id, duel.message_id
        await session.commit()
    await safe_edit_text(
        bot, "⌛ <b>Вызов истёк</b> — соперник не ответил вовремя.",
        chat_id=chat_id, message_id=message_id, parse_mode="HTML",
    )
