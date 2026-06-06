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
async def accept_duel(bot: Bot, duel_id: int, user_id: int, osu_api,
                      event_chat_id: Optional[int] = None) -> bool:
    """Accept a pending duel: seed ratings, build the level-matched pool, open
    the IRC room, then hand off to the round engine. IRC is required.

    ``event_chat_id`` (the chat the accept came from) is verified against the
    duel's own ``chat_id`` — a multi-tenant defense-in-depth so an action can't
    cross the group boundary the duel was created in."""
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.player2_user_id != user_id:
            return False
        if event_chat_id is not None and duel.chat_id != event_chat_id:
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
    # excludes pool_a's ids). Each round the active player interactively picks a
    # map from their remaining pool (weaker serves first, then alternating) — so
    # we persist both pools as "p1ids;p2ids" and let the round engine drive the
    # pick phase. Who serves first is recomputed there from ratings.
    pool_a = await get_pick_candidates(target_sr, n=DUEL_POOL_MAPS)
    pool_b = await get_pick_candidates(
        target_sr, n=DUEL_POOL_MAPS,
        exclude_ids=[m.beatmap_id for m in pool_a],
    )
    if not pool_a or not pool_b:
        await _abort(bot, duel_id, chat_id, message_id,
                     "❌ В пуле недостаточно карт для двух наборов — дуэль отменена.")
        return True

    # Assign pools: pool_a → weaker player, pool_b → stronger; store as p1;p2.
    weaker_is_p1 = (r1.conservative, r1.mu) <= (r2.conservative, r2.mu)
    p1_pool, p2_pool = (pool_a, pool_b) if weaker_is_p1 else (pool_b, pool_a)
    pool_field = ";".join([
        ",".join(str(m.beatmap_id) for m in p1_pool),
        ",".join(str(m.beatmap_id) for m in p2_pool),
    ])

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
        duel.pool_beatmap_ids = pool_field
        duel.status = 'round_active'
        await session.commit()

    # The intermediate "✅ Вызов принят" GROUP notice is auto-deleted once the
    # live status card is up: it's only useful for the seconds between "accept"
    # and "card", and it clutters the duel topic everyone reads. The original
    # challenge message stays as conversation history.
    #
    # NOTE: the per-player pool DM card is deliberately NOT enrolled here — it's
    # each player's private reference for the whole duel (the pick prompts say
    # "выбери карту по номеру, как на карточке пула"), so deleting it would leave
    # them with nothing to read. It lives in a private DM and never touches the
    # group topic, so the clutter rationale doesn't apply to it.
    intermediate_msg_ids: list[tuple[int, int]] = []  # (chat_id, message_id)

    try:
        msg = await bot.send_message(
            chat_id,
            f"✅ Вызов принят! Комната создана. У каждого свой набор из "
            f"<b>{DUEL_POOL_MAPS}</b> карт под средний уровень (~{target_sr:.1f}★). "
            f"Каждый раунд игроки по очереди выбирают карту из своего пула "
            f"(слабее по рейтингу — первым).\nПул и кнопки выбора придут вам "
            f"в личку — принимайте инвайт в osu! и играйте.",
            parse_mode="HTML", message_thread_id=thread_id,
        )
        intermediate_msg_ids.append((chat_id, msg.message_id))
    except Exception:
        logger.debug(f"accept_duel: start notice failed for duel {duel_id}", exc_info=True)

    # DM each player their OWN pool as a LIVE card. `pool_card` owns this message
    # for the whole duel: the swap and per-round pick keyboards ride on it, and
    # played maps get stamped — so the player has a single control surface
    # instead of a static picture plus separate prompt messages.
    try:
        from services.duel import pool_card

        async def _send_pool(u, pool_rows):
            if not u or not u.telegram_id or not pool_rows:
                return
            order = [m.beatmap_id for m in pool_rows]
            pool_card.ensure(duel_id, u.telegram_id, chat_id=u.telegram_id,
                             order=order, mode=mode, target_sr=target_sr)
            await pool_card.show(
                bot, duel_id, u.telegram_id,
                caption=(
                    "⚔️ <b>Твой пул собран</b> — 6 карт под средний уровень обоих "
                    "игроков. Сейчас откроется подгонка: сможешь заменить пару "
                    "карт. Кнопки выбора будут на этой карточке. Принимай инвайт "
                    "в osu! и играй."
                ),
            )

        await asyncio.gather(_send_pool(p1, p1_pool), _send_pool(p2, p2_pool))
    except Exception:
        logger.debug(f"accept_duel: pool card build failed for duel {duel_id}", exc_info=True)

    # Post the live scoreboard card; the round engine edits it in place.
    try:
        from services.duel import status_card
        await status_card.post_or_update(bot, duel_id)
    except Exception:
        logger.debug(f"accept_duel: status card post failed for duel {duel_id}", exc_info=True)

    # Live card is up — drop the now-redundant intermediate messages.
    for cid, mid in intermediate_msg_ids:
        try:
            await bot.delete_message(cid, mid)
        except Exception:
            logger.debug(f"accept_duel: cleanup delete ({cid},{mid}) failed", exc_info=True)

    round_engine.launch(bot, osu_api, duel_id)
    return True


async def decline_duel(bot: Bot, duel_id: int, user_id: int,
                       event_chat_id: Optional[int] = None) -> bool:
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status != 'pending' or duel.player2_user_id != user_id:
            return False
        if event_chat_id is not None and duel.chat_id != event_chat_id:
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
async def cancel_duel(bot: Bot, duel_id: int, user_id: int,
                      event_chat_id: Optional[int] = None) -> str:
    """Cancel a duel the user participates in.
    Returns 'cancelled' | 'not_found' | 'not_participant'."""
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status not in ('pending', 'accepted', 'round_active'):
            return 'not_found'
        if user_id not in (duel.player1_user_id, duel.player2_user_id):
            return 'not_participant'
        if event_chat_id is not None and duel.chat_id != event_chat_id:
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

    # Drop every trace of the duel: unblock the engine on its pick wait, clear
    # the live card mapping, and forget reconnect tracking.
    from services.duel.duel_state import clear_duel_state
    clear_duel_state(duel_id)

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
