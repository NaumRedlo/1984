"""BSK duel recovery after bot restart."""
import asyncio
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot
from sqlalchemy import select

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User
from services.bsk.duel_state import pool_state as _pool_state
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.telegram_safe import safe_edit_text

logger = get_logger("bsk.duel_recover")


async def _get_user(session, user_id: int):
    return (await session.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()


async def recover_active_duels(bot: Bot, osu_api) -> None:
    logger.info("recover_active_duels: scanning for active duels...")

    async with get_db_session() as session:
        duels = (await session.execute(
            select(BskDuel).where(
                BskDuel.status.in_(['pending', 'accepted', 'round_active'])
            )
        )).scalars().all()
        duel_infos = [(d.id, d.status) for d in duels]

    if not duel_infos:
        logger.info("recover_active_duels: no active duels found")
        return

    logger.info(f"recover_active_duels: found {len(duel_infos)} active duels")

    for duel_id, status in duel_infos:
        try:
            if status == 'pending':
                await _recover_pending(bot, duel_id, osu_api)
            elif status == 'round_active':
                await _recover_round_active(bot, duel_id, osu_api)
            elif status == 'accepted':
                await _recover_accepted(bot, duel_id, osu_api)
        except Exception as e:
            logger.error(
                f"recover_active_duels: failed to recover duel {duel_id} "
                f"(status={status}): {e}",
                exc_info=True,
            )

    logger.info("recover_active_duels: recovery complete")


async def _expire_duel_at(bot: Bot, duel_id: int, osu_api, expires_at: datetime) -> None:
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = max(0, (expires_at - now).total_seconds())
    if remaining > 0:
        await asyncio.sleep(remaining)

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'pending':
            return
        duel.status = 'expired'
        p2 = await _get_user(session, duel.player2_user_id)
        await session.commit()

        opp_name = escape_html(p2.osu_username) if p2 else "Соперник"
        await safe_edit_text(
            bot,
            f"⏰ <b>Вызов истёк</b>\n\n"
            f"<i>{opp_name} не ответил вовремя.</i>\n"
            "Дуэль отменена.",
            chat_id=duel.chat_id,
            message_id=duel.message_id,
            parse_mode="HTML",
        )


async def _recover_pending(bot: Bot, duel_id: int, osu_api) -> None:
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'pending':
            return

        now = datetime.now(timezone.utc)
        expires = duel.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)

        if not expires or now > expires:
            duel.status = 'expired'
            await session.commit()
            logger.info(f"_recover_pending: duel {duel_id} expired")
            await safe_edit_text(
                bot,
                "⏰ <b>Вызов истёк</b>\n\n"
                "<i>Бот был перезапущен, вызов не был принят вовремя.</i>\n"
                "Дуэль отменена.",
                chat_id=duel.chat_id,
                message_id=duel.message_id,
                parse_mode="HTML",
            )
            return

    logger.info(f"_recover_pending: duel {duel_id}, {(expires - now).total_seconds():.0f}s remaining")
    asyncio.create_task(_expire_duel_at(bot, duel_id, osu_api, expires))


async def _recover_round_active(bot: Bot, duel_id: int, osu_api) -> None:
    from services.bsk.duel_round import _safe_monitor_round, _post_round_routing
    from services.bsk.duel_pick import _send_pick_to_active_player
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'round_active':
            return

        rnd = (await session.execute(
            select(BskDuelRound).where(
                BskDuelRound.duel_id == duel_id,
                BskDuelRound.status == 'waiting',
            ).order_by(BskDuelRound.round_number.desc())
        )).scalar_one_or_none()

        chat_id = duel.chat_id
        thread_id = duel.message_thread_id
        has_pool = bool(duel.pick_candidates_p1 or duel.pick_candidates_p2 or duel.pick_candidates)

    if not rnd:
        logger.warning(
            f"_recover_round_active: duel {duel_id} is round_active "
            f"but has no waiting round, routing to post-round"
        )
        if has_pool:
            await _reconstruct_pool_state(bot, duel_id)
        asyncio.create_task(_post_round_routing(bot, duel_id, 0))
        return

    round_id = rnd.id
    logger.info(f"_recover_round_active: duel {duel_id} round {round_id} — restarting monitor")

    if has_pool:
        await _reconstruct_pool_state(bot, duel_id)

    try:
        await bot.send_message(
            chat_id,
            "🔄 <b>Бот перезапущен</b> — дуэль продолжается.\n"
            "Мониторинг очков возобновлён.",
            parse_mode="HTML",
            message_thread_id=thread_id,
        )
    except Exception:
        logger.debug(f"_recover_round_active: restart notice send failed for duel {duel_id}", exc_info=True)

    asyncio.create_task(_safe_monitor_round(bot, duel_id, round_id, osu_api))


async def _recover_accepted(bot: Bot, duel_id: int, osu_api) -> None:
    from services.bsk.duel_pick import _start_pick_phase
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'accepted':
            return

        has_candidates = bool(duel.pick_candidates_p1 or duel.pick_candidates_p2 or duel.pick_candidates)
        has_turn = duel.pick_turn is not None
        chat_id = duel.chat_id
        thread_id = duel.message_thread_id

    if has_candidates and has_turn:
        logger.info(f"_recover_accepted: duel {duel_id} mid-pick, reconstructing pool state")
        try:
            await bot.send_message(
                chat_id,
                "🔄 <b>Бот перезапущен</b> — фаза выбора карты возобновлена.",
                parse_mode="HTML",
                message_thread_id=thread_id,
            )
        except Exception:
            logger.debug(f"_recover_accepted: pick-resume notice send failed for duel {duel_id}", exc_info=True)
        await _reconstruct_pool_and_resume_pick(bot, duel_id, osu_api)
    else:
        logger.info(f"_recover_accepted: duel {duel_id} mid-ban/pre-ban, skipping to fresh pick")
        try:
            await bot.send_message(
                chat_id,
                "🔄 <b>Бот был перезапущен во время фазы бана.</b>\n\n"
                "К сожалению, ваши баны не сохраняются между перезапусками. "
                "Начинаем выбор карты заново — банов на этот раунд не будет.",
                parse_mode="HTML",
                message_thread_id=thread_id,
            )
        except Exception:
            logger.debug(f"_recover_accepted: ban-restart notice send failed for duel {duel_id}", exc_info=True)
        async with get_db_session() as session:
            duel = (await session.execute(
                select(BskDuel).where(BskDuel.id == duel_id)
            )).scalar_one_or_none()
            if duel:
                duel.pick_candidates = None
                duel.pick_candidates_p1 = None
                duel.pick_candidates_p2 = None
                duel.pick_p1 = None
                duel.pick_p2 = None
                duel.pick_turn = None
                duel.pick_played = None
                await session.commit()
        await _start_pick_phase(bot, duel_id, osu_api)


async def _reconstruct_pool_state(bot: Bot, duel_id: int) -> bool:
    """Rebuild _pool_state[duel_id] from DB. Returns True if successful."""
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or (not duel.pick_candidates_p1 and not duel.pick_candidates_p2):
            return False

        p1_ids = [int(x) for x in (duel.pick_candidates_p1 or "").split(",") if x]
        p2_ids = [int(x) for x in (duel.pick_candidates_p2 or "").split(",") if x]
        if not p1_ids and not p2_ids:
            return False

        all_ids = list(set(p1_ids) | set(p2_ids))
        pool_rows = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id.in_(all_ids))
        )).scalars().all()
        rows_by_id = {m.beatmap_id: m for m in pool_rows}
        ordered_p1 = [rows_by_id[bid] for bid in p1_ids if bid in rows_by_id]
        ordered_p2 = [rows_by_id[bid] for bid in p2_ids if bid in rows_by_id]

        p1 = await _get_user(session, duel.player1_user_id)
        p2 = await _get_user(session, duel.player2_user_id)

        def _to_dm(m):
            return {
                'beatmap_id':    m.beatmap_id,
                'beatmapset_id': m.beatmapset_id,
                'title':         m.title,
                'artist':        m.artist,
                'version':       m.version,
                'star_rating':   float(m.star_rating or 0),
                'map_type':      m.map_type or '',
                'ar':            m.ar,
                'od':            m.od,
                'cs':            m.cs,
                'hp':            m.hp_drain if m.hp_drain is not None else 0,
                'bpm':           m.bpm,
                'drain_time':    m.length,
            }

        dm_candidates_p1 = [_to_dm(m) for m in ordered_p1]
        dm_candidates_p2 = [_to_dm(m) for m in ordered_p2]
        group_candidates_p1 = [{'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''} for m in ordered_p1]
        group_candidates_p2 = [{'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''} for m in ordered_p2]

        _pool_state[duel_id] = {
            'p1_tg_id':          p1.telegram_id if p1 else None,
            'p2_tg_id':          p2.telegram_id if p2 else None,
            'dm_candidates_p1':    dm_candidates_p1,
            'dm_candidates_p2':    dm_candidates_p2,
            'group_candidates_p1': group_candidates_p1,
            'group_candidates_p2': group_candidates_p2,
            'round_num':         duel.current_round + 1,
            'p1_name':           p1.osu_username if p1 else 'Player 1',
            'p2_name':           p2.osu_username if p2 else 'Player 2',
            'p1_country':        (p1.country or '') if p1 else '',
            'p2_country':        (p2.country or '') if p2 else '',
            'p1_cover_url':      (p1.cover_url or '') if p1 else '',
            'p2_cover_url':      (p2.cover_url or '') if p2 else '',
            'is_test':           duel.is_test,
            'active_pick_dm_msg': None,
            'active_pick_tg_id':  None,
        }
        logger.info(f"_reconstruct_pool_state: rebuilt pool for duel {duel_id} (p1={len(dm_candidates_p1)}, p2={len(dm_candidates_p2)} maps)")
    return True


async def _reconstruct_pool_and_resume_pick(bot: Bot, duel_id: int, osu_api) -> None:
    from services.bsk.duel_pick import _send_pick_to_active_player
    ok = await _reconstruct_pool_state(bot, duel_id)
    if not ok:
        logger.warning(f"_reconstruct_pool_and_resume_pick: duel {duel_id} has no pool to rebuild")
        return
    await _send_pick_to_active_player(bot, duel_id, osu_api)