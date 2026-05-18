"""BSK round lifecycle: start, monitor, complete, forfeit, routing."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, update as sa_update

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User
from services.bsk.composite import composite_score, composite_points, points_multiplier_for
from services.bsk.duel_constants import (
    RANKED_BAN_PHASE_ROUNDS, SCORE_POLL_INTERVAL,
    _base_sr_for_duel, _forfeit_deadline,
    _max_rounds_for, _round_multiplier_for, _target_score_for_mode,
)
from services.bsk.duel_state import pool_state as _pool_state
from services.bsk.duel_telegram import send_or_edit_photo as _send_or_edit_photo
from services.bsk.duel_ui import beatmap_links as _beatmap_links
from services.bsk.map_selector import get_map_for_round, next_star_rating
from services.bsk.ml_inference import predict_round_winner
from services.bsk.rating import update_ratings
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.telegram_safe import safe_edit_text

logger = get_logger("bsk.duel_round")

MAX_MONITOR_HOURS = 2


async def _get_user(session, user_id: int):
    return (await session.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()

async def _safe_monitor_round(bot: Bot, duel_id: int, round_id: int, osu_api) -> None:
    """Wrapper that ensures _monitor_round exceptions are logged, not silently swallowed."""
    try:
        await _monitor_round(bot, duel_id, round_id, osu_api)
    except Exception as e:
        logger.error(
            f"_monitor_round crashed for duel {duel_id} round {round_id}: {e}",
            exc_info=True,
        )


async def _start_next_round(
    bot: Bot, duel_id: int, osu_api,
    forced_map: Optional["BskMapPool"] = None,
) -> None:
    from services.bsk.duel_finish import _finish_duel
    # Initialise locals that must survive outside the DB session
    _round_entry_id: Optional[int] = None
    _chat_id = _message_id = _current_round = _beatmap_id = None
    _is_test = False
    round_card_data: dict = {}
    forfeit_mins = 15
    pause_kb = None

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status not in ('accepted', 'round_active'):
            logger.debug(f"_start_next_round: duel {duel_id} skip status={duel.status if duel else 'None'}")
            return

        logger.debug(f"_start_next_round: duel {duel_id} round={duel.current_round} scores={int(duel.player1_total_score)}/{int(duel.player2_total_score)}")

        if max(duel.player1_total_score, duel.player2_total_score) >= duel.target_score:
            await _finish_duel(bot, duel_id)
            return

        _max_rounds = _max_rounds_for(duel.mode)
        if _max_rounds is not None and duel.current_round >= _max_rounds:
            await _finish_duel(bot, duel_id)
            return

        # Get played map ids
        played = (await session.execute(
            select(BskDuelRound.beatmap_id).where(BskDuelRound.duel_id == duel_id)
        )).scalars().all()

        # Determine target SR
        from services.bsk.rating import get_or_create_rating
        _p1_usr = await _get_user(session, duel.player1_user_id)
        _p2_usr = await _get_user(session, duel.player2_user_id)
        r1 = await get_or_create_rating(
            duel.player1_user_id, duel.mode,
            player_pp=float(_p1_usr.player_pp or 0) if _p1_usr else 0.0,
        )
        r2 = await get_or_create_rating(
            duel.player2_user_id, duel.mode,
            player_pp=float(_p2_usr.player_pp or 0) if _p2_usr else 0.0,
        )

        if duel.current_round == 0:
            duel.current_star_rating = _base_sr_for_duel(r1, r2, duel.mode)
        base_sr = duel.current_star_rating

        target_sr = base_sr + duel.pressure_offset
        beatmap = forced_map or await get_map_for_round(target_sr, exclude_ids=list(played))

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
        p1_name = p1.osu_username if p1 else "Игрок 1"
        p2_name = p2.osu_username if p2 else "Игрок 2"
        p1_country = (p1.country or '') if p1 else ''
        p2_country = (p2.country or '') if p2 else ''

        forfeit_mins = (beatmap.length or 180) // 60 + 15

        control_row = [InlineKeyboardButton(text="⏸ Пауза", callback_data=f"bskd:pause:{duel_id}")]
        if duel.is_test:
            control_row.append(InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=f"bskd:test_cancel:{duel_id}",
            ))
        kb_rows = [control_row]
        _irc_active = False
        from services.bancho_irc import get_irc_client as _get_irc
        if _get_irc().connected and duel.osu_match_id:
            _irc_active = True
        elif not duel.osu_match_id:
            kb_rows.insert(0, [InlineKeyboardButton(
                text="📨 Прислать ссылку на лобби",
                callback_data=f"bskd:setmatch:{duel_id}",
            )])
        pause_kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        _has_match_id = duel.osu_match_id is not None

        # Build round start card
        from services.image import card_renderer

        _card_round_mult = _round_multiplier_for(duel.mode, duel.current_round)
        _card_max_rounds = _max_rounds_for(duel.mode)
        _rounds_for_score = (await session.execute(
            select(BskDuelRound).where(BskDuelRound.duel_id == duel_id)
        )).scalars().all()
        _p1_round_wins = sum(1 for _r in _rounds_for_score if _r.winner_player == 1)
        _p2_round_wins = sum(1 for _r in _rounds_for_score if _r.winner_player == 2)

        round_card_data = {
            'round_number': duel.current_round,
            'max_rounds': _card_max_rounds,
            'mode': duel.mode,
            'round_multiplier': _card_round_mult,
            'status': 'live',
            'p1_name': p1_name,
            'p2_name': p2_name,
            'p1_country': p1_country,
            'p2_country': p2_country,
            'p1_cover_url': (p1.cover_url or '') if p1 else '',
            'p2_cover_url': (p2.cover_url or '') if p2 else '',
            'p1_round_wins': _p1_round_wins,
            'p2_round_wins': _p2_round_wins,
            'p1_mu_aim':   r1.mu_aim   if r1 else 250.0,
            'p1_mu_speed': r1.mu_speed if r1 else 250.0,
            'p1_mu_acc':   r1.mu_acc   if r1 else 250.0,
            'p1_mu_cons':  r1.mu_cons  if r1 else 250.0,
            'p2_mu_aim':   r2.mu_aim   if r2 else 250.0,
            'p2_mu_speed': r2.mu_speed if r2 else 250.0,
            'p2_mu_acc':   r2.mu_acc   if r2 else 250.0,
            'p2_mu_cons':  r2.mu_cons  if r2 else 250.0,
            'star_rating': float(beatmap.star_rating or 0),
            'beatmapset_id': beatmap.beatmapset_id,
            'beatmap_title': f"{beatmap.artist} - {beatmap.title} [{beatmap.version}]",
            'beatmap_artist': beatmap.artist or '',
            'beatmap_name': beatmap.title or '',
            'beatmap_version': beatmap.version or '',
            'beatmap_creator': beatmap.creator or '',
            'map_type': beatmap.map_type or '',
            'bpm': float(beatmap.bpm or 0) or None,
            'length_seconds': beatmap.length,
            'score_p1': int(duel.player1_total_score),
            'score_p2': int(duel.player2_total_score),
            'target_score': int(duel.target_score),
        }
        # Capture all values needed outside this session before it closes
        _round_entry_id = round_entry.id
        _chat_id       = duel.chat_id
        _message_id    = duel.message_id
        _thread_id     = duel.message_thread_id
        _current_round = duel.current_round
        _is_test       = duel.is_test
        _beatmap_id    = beatmap.beatmap_id
        _beatmapset_id = beatmap.beatmapset_id or 0
        _mode          = duel.mode

    # ── Outside session: card rendering + Telegram IO ──────────────────────
    # Any exception here must NOT prevent the monitor task from starting.
    try:
        img_bytes = await card_renderer.generate_bsk_round_start_card_async(round_card_data)
        test_tag = ' [ТЕСТ]' if _is_test else ''
        round_mult = _round_multiplier_for(_mode, _current_round)
        mult_tag = f" • ×{round_mult:.2f}" if round_mult > 1.0 else ""
        if _irc_active:
            caption = (
                f"🎮 <b>Раунд {_current_round}{test_tag}{mult_tag}</b>\n"
                f"⏱ Форфейт через <b>{forfeit_mins} мин</b>"
            )
        else:
            match_line = (
                "📨 <i>Создайте multi-лобби и пришлите ссылку — кнопка ниже.</i>\n"
                if not _has_match_id else ""
            )
            caption = (
                f"🎮 <b>Раунд {_current_round}{test_tag}{mult_tag}</b>\n"
                f"🔗 {_beatmap_links(_beatmap_id, _beatmapset_id)}\n"
                f"{match_line}"
                f"⏱ Форфейт через <b>{forfeit_mins} мин</b>"
            )
        new_mid = await _send_or_edit_photo(
            bot, _chat_id, _message_id,
            img_bytes, caption=caption, reply_markup=pause_kb,
            thread_id=_thread_id,
        )
        if new_mid != _message_id:
            async with get_db_session() as _sess2:
                _d = (await _sess2.execute(
                    select(BskDuel).where(BskDuel.id == duel_id)
                )).scalar_one_or_none()
                if _d:
                    _d.message_id = new_mid
                    await _sess2.commit()
    except Exception as e:
        logger.error(
            f"_start_next_round: card/send failed for duel {duel_id}: {e}",
            exc_info=True,
        )

    # ── Always start the score-monitoring task ──────────────────────────────
    if _round_entry_id:
        from services.bancho_irc import get_irc_client
        irc = get_irc_client()
        if irc.connected and _has_match_id:
            from services.bsk.irc_room import set_map_and_start
            asyncio.create_task(_irc_start_and_monitor(
                bot, duel_id, _round_entry_id, osu_api, irc, _beatmap_id,
            ))
        else:
            asyncio.create_task(_safe_monitor_round(bot, duel_id, _round_entry_id, osu_api))


MAX_MONITOR_HOURS = 2


async def _irc_start_and_monitor(
    bot: Bot, duel_id: int, round_id: int, osu_api,
    irc, beatmap_id: int,
) -> None:
    """Set map via IRC, wait for match_finished event, then fetch results from API."""
    from services.bsk.irc_room import set_map_and_start
    from services.bsk.match_monitor import extract_score_stats, find_round_score

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        rnd = (await session.execute(
            select(BskDuelRound).where(BskDuelRound.id == round_id)
        )).scalar_one_or_none()
        if not duel or not rnd:
            return
        match_id = duel.osu_match_id
        forfeit_at = rnd.forfeit_at
        if forfeit_at and forfeit_at.tzinfo is None:
            forfeit_at = forfeit_at.replace(tzinfo=timezone.utc)

    if not match_id:
        logger.warning(f"_irc_start_and_monitor: no match_id for duel {duel_id}, falling back")
        await _safe_monitor_round(bot, duel_id, round_id, osu_api)
        return

    try:
        await set_map_and_start(irc, int(match_id), beatmap_id, countdown=90)
    except Exception as e:
        logger.error(f"_irc_start_and_monitor: set_map_and_start failed: {e}")
        await _safe_monitor_round(bot, duel_id, round_id, osu_api)
        return

    channel = f"#mp_{match_id}"
    match_finished = asyncio.Event()

    async def _on_finish(ch: str, text: str):
        if ch == channel:
            match_finished.set()

    irc.on("match_finished", _on_finish)

    try:
        timeout_secs = MAX_MONITOR_HOURS * 3600
        if forfeit_at:
            remaining = (forfeit_at - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                timeout_secs = remaining

        await asyncio.wait_for(match_finished.wait(), timeout=timeout_secs)
    except asyncio.TimeoutError:
        logger.info(f"_irc_start_and_monitor: timeout/forfeit for duel {duel_id} round {round_id}")
        async with get_db_session() as session:
            rnd = (await session.execute(
                select(BskDuelRound).where(BskDuelRound.id == round_id)
            )).scalar_one_or_none()
            duel = (await session.execute(
                select(BskDuel).where(BskDuel.id == duel_id)
            )).scalar_one_or_none()
            if rnd and rnd.status == 'waiting' and duel:
                await _handle_forfeit(bot, duel, rnd, session)
                await session.commit()
        return
    finally:
        try:
            irc._handlers.get("match_finished", []).remove(_on_finish)
        except ValueError:
            pass

    await asyncio.sleep(3)

    async with get_db_session() as session:
        rnd = (await session.execute(
            select(BskDuelRound).where(BskDuelRound.id == round_id)
        )).scalar_one_or_none()
        if not rnd or rnd.status != 'waiting':
            return
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status not in ('accepted', 'round_active'):
            return

        p1 = await _get_user(session, duel.player1_user_id)
        p2 = await _get_user(session, duel.player2_user_id)
        if not p1 or not p2 or not p1.osu_user_id or not p2.osu_user_id:
            return

    try:
        payload = await osu_api.get_match(int(match_id))
    except Exception as e:
        logger.error(f"_irc_start_and_monitor: get_match failed: {e}")
        return
    if not payload:
        return

    started_at = rnd.started_at
    if started_at and started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    result = find_round_score(
        payload,
        beatmap_id=rnd.beatmap_id,
        p1_osu_id=p1.osu_user_id,
        p2_osu_id=p2.osu_user_id,
        after=started_at,
    )
    if not result:
        logger.warning(f"_irc_start_and_monitor: no scores found in match {match_id} for round {round_id}")
        return

    p1_raw, p2_raw = result
    p1_stats = extract_score_stats(p1_raw)
    p2_stats = extract_score_stats(p2_raw)

    beatmap_max_combo = 0
    try:
        bm = await osu_api.get_beatmap(rnd.beatmap_id)
        if bm:
            beatmap_max_combo = int(bm.get("max_combo") or 0)
    except Exception:
        pass
    if beatmap_max_combo <= 0:
        beatmap_max_combo = max(p1_stats["combo"], p2_stats["combo"], 1)

    async with get_db_session() as session:
        rnd = (await session.execute(
            select(BskDuelRound).where(BskDuelRound.id == round_id)
        )).scalar_one_or_none()
        if not rnd or rnd.status != 'waiting':
            return
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status not in ('accepted', 'round_active'):
            return

        for player_num, st in [(1, p1_stats), (2, p2_stats)]:
            if getattr(rnd, f'player{player_num}_composite') is not None:
                continue

            comp = composite_score(st["accuracy"], st["combo"], beatmap_max_combo, st["misses"])
            pts = composite_points(
                st["accuracy"], st["combo"], beatmap_max_combo, st["misses"],
                passed=st["passed"], mode=duel.mode,
            )

            setattr(rnd, f'player{player_num}_score', st["score"])
            setattr(rnd, f'player{player_num}_accuracy', st["accuracy"])
            setattr(rnd, f'player{player_num}_combo', st["combo"])
            setattr(rnd, f'player{player_num}_misses', st["misses"])
            setattr(rnd, f'player{player_num}_pp', 0.0)
            setattr(rnd, f'player{player_num}_composite', comp)
            setattr(rnd, f'player{player_num}_points', pts)
            setattr(rnd, f'player{player_num}_submitted_at', datetime.now(timezone.utc))

        if rnd.player1_composite is not None and rnd.player2_composite is not None:
            await _complete_round(bot, duel, rnd, session)
            return

        await session.commit()


async def _monitor_round(bot: Bot, duel_id: int, round_id: int, osu_api) -> None:
    """Poll the linked osu! match for both players' scores on the round map.

    Fetches /matches/{osu_match_id} once per cycle and looks for the first
    completed game on rnd.beatmap_id, started at-or-after rnd.started_at,
    where both players submitted. Failed passes count, so NoFail is no longer
    required. If `osu_match_id` is not yet set on the duel, the loop just
    waits — `_handle_forfeit` triggers via `forfeit_at` if the link never
    arrives.
    """
    from services.bsk.match_monitor import (
        extract_score_stats,
        find_round_score,
    )

    deadline = datetime.now(timezone.utc) + timedelta(hours=MAX_MONITOR_HOURS)

    while True:
        await asyncio.sleep(SCORE_POLL_INTERVAL)

        if datetime.now(timezone.utc) > deadline:
            logger.error(f"_monitor_round: hard timeout ({MAX_MONITOR_HOURS}h) for duel {duel_id} round {round_id}")
            async with get_db_session() as session:
                rnd = (await session.execute(
                    select(BskDuelRound).where(BskDuelRound.id == round_id)
                )).scalar_one_or_none()
                duel = (await session.execute(
                    select(BskDuel).where(BskDuel.id == duel_id)
                )).scalar_one_or_none()
                if rnd and rnd.status == 'waiting' and duel:
                    await _handle_forfeit(bot, duel, rnd, session)
                    await session.commit()
            return

        async with get_db_session() as session:
            rnd = (await session.execute(
                select(BskDuelRound).where(BskDuelRound.id == round_id)
            )).scalar_one_or_none()
            if not rnd or rnd.status != 'waiting':
                return

            duel = (await session.execute(
                select(BskDuel).where(BskDuel.id == duel_id)
            )).scalar_one_or_none()
            if not duel or duel.status not in ('accepted', 'round_active'):
                return

            now = datetime.now(timezone.utc)
            forfeit_at = rnd.forfeit_at
            if forfeit_at and forfeit_at.tzinfo is None:
                forfeit_at = forfeit_at.replace(tzinfo=timezone.utc)

            if forfeit_at and now > forfeit_at:
                await _handle_forfeit(bot, duel, rnd, session)
                await session.commit()
                return

            match_id = duel.osu_match_id
            if not match_id:
                # Players haven't linked the multi yet — keep waiting; the
                # forfeit_at branch above eventually fires if they never do.
                continue

            p1 = await _get_user(session, duel.player1_user_id)
            p2 = await _get_user(session, duel.player2_user_id)
            if not p1 or not p2 or not p1.osu_user_id or not p2.osu_user_id:
                continue

        # Fetch match payload outside the DB transaction — a single network
        # request, two players' results extracted at once.
        try:
            payload = await osu_api.get_match(int(match_id))
        except Exception as e:
            logger.warning(f"_monitor_round: get_match({match_id}) failed: {e}")
            continue
        if not payload:
            continue

        started_at = rnd.started_at
        if started_at and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        result = find_round_score(
            payload,
            beatmap_id=rnd.beatmap_id,
            p1_osu_id=p1.osu_user_id,
            p2_osu_id=p2.osu_user_id,
            after=started_at,
        )
        if not result:
            continue

        p1_raw, p2_raw = result
        p1_stats = extract_score_stats(p1_raw)
        p2_stats = extract_score_stats(p2_raw)

        # max_combo of the beatmap is needed for composite scoring.
        beatmap_max_combo = 0
        try:
            bm = await osu_api.get_beatmap(rnd.beatmap_id)
            if bm:
                beatmap_max_combo = int(bm.get("max_combo") or 0)
        except Exception as e:
            logger.warning(f"_monitor_round: get_beatmap({rnd.beatmap_id}) failed: {e}")
        if beatmap_max_combo <= 0:
            beatmap_max_combo = max(p1_stats["combo"], p2_stats["combo"], 1)

        async with get_db_session() as session:
            rnd = (await session.execute(
                select(BskDuelRound).where(BskDuelRound.id == round_id)
            )).scalar_one_or_none()
            if not rnd or rnd.status != 'waiting':
                return
            duel = (await session.execute(
                select(BskDuel).where(BskDuel.id == duel_id)
            )).scalar_one_or_none()
            if not duel or duel.status not in ('accepted', 'round_active'):
                return

            for player_num, st in [(1, p1_stats), (2, p2_stats)]:
                if getattr(rnd, f'player{player_num}_composite') is not None:
                    continue

                comp = composite_score(st["accuracy"], st["combo"], beatmap_max_combo, st["misses"])
                pts = composite_points(
                    st["accuracy"],
                    st["combo"],
                    beatmap_max_combo,
                    st["misses"],
                    passed=st["passed"],
                    mode=duel.mode,
                )

                setattr(rnd, f'player{player_num}_score',     st["score"])
                setattr(rnd, f'player{player_num}_accuracy',  st["accuracy"])
                setattr(rnd, f'player{player_num}_combo',     st["combo"])
                setattr(rnd, f'player{player_num}_misses',    st["misses"])
                setattr(rnd, f'player{player_num}_pp',        0.0)
                setattr(rnd, f'player{player_num}_composite', comp)
                setattr(rnd, f'player{player_num}_points',    pts)
                setattr(rnd, f'player{player_num}_submitted_at', datetime.now(timezone.utc))

            if rnd.player1_composite is not None and rnd.player2_composite is not None:
                await _complete_round(bot, duel, rnd, session)
                return

            await session.commit()


async def _complete_round(bot: Bot, duel: BskDuel, rnd: BskDuelRound, session) -> None:
    # Atomic CAS: only one caller can transition this round out of waiting/active.
    # Prevents two near-simultaneous monitors from double-crediting total_score.
    result = await session.execute(
        sa_update(BskDuelRound)
        .where(
            BskDuelRound.id == rnd.id,
            BskDuelRound.status.in_(('waiting', 'active')),
        )
        .values(status='completed')
    )
    if result.rowcount == 0:
        logger.info(f"_complete_round: round {rnd.id} already finalized — skipping double-credit")
        return
    await session.refresh(rnd)

    c1 = rnd.player1_composite or 0
    c2 = rnd.player2_composite or 0
    # Fallback: derive points from already-normalized composite (which used the
    # correct beatmap.max_combo when the score was ingested).  Re-running
    # composite_points here without the real max_combo produces a combo_ratio
    # of 1.0 and grossly inflated points.
    pts_scale = points_multiplier_for(duel.mode)
    pts1 = rnd.player1_points if rnd.player1_points is not None else int(c1 * pts_scale)
    pts2 = rnd.player2_points if rnd.player2_points is not None else int(c2 * pts_scale)
    mult = _round_multiplier_for(duel.mode, rnd.round_number)
    if mult > 1.0:
        pts1 = int(pts1 * mult)
        pts2 = int(pts2 * mult)
    rnd.player1_points = pts1
    rnd.player2_points = pts2

    if c1 > c2:
        winner = 1
    elif c2 > c1:
        winner = 2
    else:
        winner = None  # exact tie — no winner, no rating impact below

    rnd.winner_player = winner
    rnd.status = 'completed'
    rnd.completed_at = datetime.now(timezone.utc)

    duel.player1_total_score += pts1
    duel.player2_total_score += pts2

    # Save per-round rating snapshots
    from db.models.bsk_rating import BskRating
    r1 = (await session.execute(
        select(BskRating).where(BskRating.user_id == duel.player1_user_id, BskRating.mode == duel.mode)
    )).scalar_one_or_none()
    r2 = (await session.execute(
        select(BskRating).where(BskRating.user_id == duel.player2_user_id, BskRating.mode == duel.mode)
    )).scalar_one_or_none()
    if r1:
        rnd.p1_mu_aim_before   = r1.mu_aim
        rnd.p1_mu_speed_before = r1.mu_speed
        rnd.p1_mu_acc_before   = r1.mu_acc
        rnd.p1_mu_cons_before  = r1.mu_cons
    if r2:
        rnd.p2_mu_aim_before   = r2.mu_aim
        rnd.p2_mu_speed_before = r2.mu_speed
        rnd.p2_mu_acc_before   = r2.mu_acc
        rnd.p2_mu_cons_before  = r2.mu_cons

    # ML prediction (uses before-snapshots if available, else current ratings)
    if r1 and r2:
        ml_winner, ml_conf = predict_round_winner(
            p1_mu_aim=rnd.p1_mu_aim_before or r1.mu_aim,
            p1_mu_speed=rnd.p1_mu_speed_before or r1.mu_speed,
            p1_mu_acc=rnd.p1_mu_acc_before or r1.mu_acc,
            p1_mu_cons=rnd.p1_mu_cons_before or r1.mu_cons,
            p2_mu_aim=rnd.p2_mu_aim_before or r2.mu_aim,
            p2_mu_speed=rnd.p2_mu_speed_before or r2.mu_speed,
            p2_mu_acc=rnd.p2_mu_acc_before or r2.mu_acc,
            p2_mu_cons=rnd.p2_mu_cons_before or r2.mu_cons,
            w_aim=rnd.w_aim or 0.25,
            w_speed=rnd.w_speed or 0.25,
            w_acc=rnd.w_acc or 0.25,
            w_cons=rnd.w_cons or 0.25,
        )
        rnd.ml_predicted_winner = ml_winner
        rnd.ml_confidence = ml_conf

    # Adaptive pressure (use leading score's player as "winner" for SR-tracking
    # when an exact tie occurred — pressure logic only needs a tie-break, not
    # a real winner)
    sr_winner = winner if winner is not None else (1 if c1 >= c2 else 2)
    new_sr = next_star_rating(
        duel.current_star_rating,
        sr_winner,
        duel.player1_total_score,
        duel.player2_total_score,
        duel.current_star_rating,
    )
    duel.pressure_offset = new_sr - duel.current_star_rating

    p1 = await _get_user(session, duel.player1_user_id)
    p2 = await _get_user(session, duel.player2_user_id)
    p1_name = p1.osu_username if p1 else "Игрок 1"
    p2_name = p2.osu_username if p2 else "Игрок 2"

    # Ratings are now updated **once per duel** in _finish_duel — using
    # round-share as the Elo result so 3:0 is rewarded more than 3:2. We
    # persist the per-round map_weights on the round itself for later
    # aggregation; no per-round mu_after snapshot is written here.
    await session.commit()

    try:
        from services.image import card_renderer
        score_p1 = int(duel.player1_total_score)
        score_p2 = int(duel.player2_total_score)
        leading = max(score_p1, score_p2)
        next_line = (
            "Следующий раунд через 15 секунд…"
            if leading < duel.target_score
            else "Цель достигнута! Подводим итоги…"
        )
        result_card_data = {
            'round_number': rnd.round_number,
            'p1_name': p1_name,
            'p2_name': p2_name,
            'p1_country': (p1.country or '') if p1 else '',
            'p2_country': (p2.country or '') if p2 else '',
            'p1_cover_url': (p1.cover_url or '') if p1 else '',
            'p2_cover_url': (p2.cover_url or '') if p2 else '',
            'winner': winner,
            'p1_points': int(pts1),
            'p2_points': int(pts2),
            'p1_acc': rnd.player1_accuracy or 0.0,
            'p2_acc': rnd.player2_accuracy or 0.0,
            'p1_combo': rnd.player1_combo or 0,
            'p2_combo': rnd.player2_combo or 0,
            'p1_misses': rnd.player1_misses or 0,
            'p2_misses': rnd.player2_misses or 0,
            'score_p1': score_p1,
            'score_p2': score_p2,
            'target_score': int(duel.target_score),
            'beatmap_title': rnd.beatmap_title or 'Unknown',
            'star_rating': float(rnd.star_rating or 0),
        }
        img_bytes = await card_renderer.generate_bsk_round_result_card_async(result_card_data)
        cur_mult = _round_multiplier_for(duel.mode, rnd.round_number)
        next_mult = _round_multiplier_for(duel.mode, rnd.round_number + 1)
        mult_line = f"\n💥 Множитель этого раунда: <b>×{cur_mult:.2f}</b>"
        if next_mult > cur_mult:
            mult_line += f" → следующий: <b>×{next_mult:.2f}</b>"
        caption = f"✅ <b>Раунд {rnd.round_number} завершён!</b>{mult_line}\n{next_line}"
        await _send_or_edit_photo(
            bot, duel.chat_id, duel.message_id,
            img_bytes, caption=caption,
            thread_id=duel.message_thread_id,
        )
    except Exception as e:
        logger.error(f"_complete_round: failed to send result card: {e}")

    asyncio.create_task(_post_round_routing(bot, duel.id, 15))


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
    p1_name = p1.osu_username if p1 else "Игрок 1"
    p2_name = p2.osu_username if p2 else "Игрок 2"

    pts1_f = rnd.player1_points or 0
    pts2_f = rnd.player2_points or 0
    mult_f = _round_multiplier_for(duel.mode, rnd.round_number)
    if mult_f > 1.0:
        pts1_f = int(pts1_f * mult_f)
        pts2_f = int(pts2_f * mult_f)
        rnd.player1_points = pts1_f
        rnd.player2_points = pts2_f
    duel.player1_total_score += pts1_f
    duel.player2_total_score += pts2_f

    # Update BSK ratings for the forfeit winner (non-test, clear winner only)
    if rnd.winner_player and not duel.is_test:
        winner_uid = duel.player1_user_id if rnd.winner_player == 1 else duel.player2_user_id
        loser_uid  = duel.player2_user_id if rnd.winner_player == 1 else duel.player1_user_id
        map_weights = {
            'aim':   rnd.w_aim   or 0.25,
            'speed': rnd.w_speed or 0.25,
            'acc':   rnd.w_acc   or 0.25,
            'cons':  rnd.w_cons  or 0.25,
        }
        winner_pp = float((p1.player_pp if rnd.winner_player == 1 else p2.player_pp) or 0) if (p1 and p2) else 0.0
        loser_pp  = float((p2.player_pp if rnd.winner_player == 1 else p1.player_pp) or 0) if (p1 and p2) else 0.0
        await session.commit()  # flush before update_ratings opens its own session
        try:
            await update_ratings(
                winner_uid, loser_uid, duel.mode,
                map_weights=map_weights,
                winner_pp=winner_pp, loser_pp=loser_pp,
            )
        except Exception as e:
            logger.error(f"_handle_forfeit: update_ratings failed: {e}", exc_info=True)

    if rnd.winner_player:
        winner_name = p1_name if rnd.winner_player == 1 else p2_name
        loser_name = p2_name if rnd.winner_player == 1 else p1_name
        msg = (
            f"⏰ <b>Время вышло!</b>\n\n"
            f"<b>{escape_html(loser_name)}</b> не успел сыграть карту.\n"
            f"Раунд {rnd.round_number} засчитан <b>{escape_html(winner_name)}</b> по forfeit.\n\n"
            f"📊 Счёт: <b>{int(duel.player1_total_score):,}</b> — <b>{int(duel.player2_total_score):,}</b>"
        )
    else:
        msg = (
            f"⏰ <b>Время вышло!</b>\n\n"
            f"Оба игрока не сыграли карту — раунд аннулирован.\n\n"
            f"📊 Счёт: <b>{int(duel.player1_total_score):,}</b> — <b>{int(duel.player2_total_score):,}</b>"
        )

    await safe_edit_text(
        bot,
        msg,
        chat_id=duel.chat_id,
        message_id=duel.message_id,
        parse_mode="HTML",
    )

    asyncio.create_task(_post_round_routing(bot, duel.id, 15))


async def _post_round_routing(bot: Bot, duel_id: int, delay: int) -> None:
    """After a round: next pick from pool, random map, or finish."""
    from services.bsk.duel_finish import _finish_duel
    from services.bsk.duel_pick import _start_pick_phase, _send_pick_to_active_player
    from services.bsk.duel_manager import _osu_api
    await asyncio.sleep(delay)
    try:
        async with get_db_session() as session:
            duel = (await session.execute(
                select(BskDuel).where(BskDuel.id == duel_id)
            )).scalar_one_or_none()
            if not duel or duel.status not in ('accepted', 'round_active'):
                return

            if max(duel.player1_total_score, duel.player2_total_score) >= duel.target_score:
                await _finish_duel(bot, duel_id)
                return

            _max_rounds = _max_rounds_for(duel.mode)
            if _max_rounds is not None and duel.current_round >= _max_rounds:
                await _finish_duel(bot, duel_id)
                return

            # Ranked: re-run ban phase before the configured rounds (5/10/15/20).
            # Round 1 already runs ban via accept_duel → _start_pick_phase, so
            # we only need to inject extra phases here for subsequent rounds.
            next_round_num = duel.current_round + 1
            if (
                duel.mode == 'ranked'
                and next_round_num in RANKED_BAN_PHASE_ROUNDS
                and next_round_num != 1
            ):
                _pool_state.pop(duel_id, None)
                duel.pick_candidates = None
                duel.pick_candidates_p1 = None
                duel.pick_candidates_p2 = None
                duel.pick_played = None
                duel.pick_turn = None
                duel.pick_p1 = None
                duel.pick_p2 = None
                await session.commit()
                await _start_pick_phase(bot, duel_id, _osu_api)
                return

            # Check if the active player's pool has remaining maps.
            active_pool = (
                duel.pick_candidates_p1 if duel.pick_turn == 1
                else duel.pick_candidates_p2 if duel.pick_turn == 2
                else None
            )
            if active_pool:
                candidate_ids = [int(x) for x in active_pool.split(",") if x]
                if candidate_ids and duel.pick_turn is not None:
                    # Keep legacy mirror in sync.
                    if duel.pick_candidates != active_pool:
                        duel.pick_candidates = active_pool
                        await session.commit()
                    await _send_pick_to_active_player(bot, duel_id, _osu_api)
                    return

            # Pool exhausted — clean up and use random map.
            _pool_state.pop(duel_id, None)
            duel.pick_candidates = None
            duel.pick_candidates_p1 = None
            duel.pick_candidates_p2 = None
            duel.pick_played = None
            duel.pick_turn = None
            duel.pick_p1 = None
            duel.pick_p2 = None
            await session.commit()

        await _start_next_round(bot, duel_id, _osu_api)
    except Exception as e:
        logger.error(f"_post_round_routing error for duel {duel_id}: {e}", exc_info=True)

