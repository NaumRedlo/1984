"""BSK ban phase + pick phase."""
import asyncio
import random as _random
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import select, update as sa_update

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User
from services.bsk.duel_constants import (
    BAN_TIMEOUT_SECONDS, MAX_BANS, PICK_TIMEOUT_SECONDS, POOL_SIZE,
    RANKED_BAN_PHASE_ROUNDS,
    _base_sr_for_duel, _max_rounds_for, _round_multiplier_for,
)
from services.bsk.duel_state import ban_state as _ban_state, pool_state as _pool_state
from services.bsk.duel_telegram import send_or_edit_photo as _send_or_edit_photo
from services.bsk.duel_ui import (
    ban_keyboard as _ban_keyboard,
    format_pick_pool_links as _format_pick_pool_links,
    pick_keyboard as _pick_keyboard,
)
from services.bsk.map_selector import get_pick_candidates, get_balanced_pick_candidates
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.telegram_safe import safe_edit_caption, safe_edit_reply_markup

logger = get_logger("bsk.duel_pick")


async def _get_user(session, user_id: int):
    return (await session.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()

async def _start_pick_phase(bot: Bot, duel_id: int, osu_api) -> None:
    """Select 8 candidate maps, display the pool card, then start the ban phase."""
    from services.bsk.duel_finish import _finish_duel
    from services.bsk.duel_round import _start_next_round
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

        p1 = await _get_user(session, duel.player1_user_id)
        p2_user = await _get_user(session, duel.player2_user_id)

        from services.bsk.rating import get_or_create_rating
        r1 = await get_or_create_rating(
            duel.player1_user_id, duel.mode,
            player_pp=float(p1.player_pp or 0) if p1 else 0.0,
        )
        r2 = await get_or_create_rating(
            duel.player2_user_id, duel.mode,
            player_pp=float(p2_user.player_pp or 0) if p2_user else 0.0,
        )

        if duel.current_round == 0:
            duel.current_star_rating = _base_sr_for_duel(r1, r2, duel.mode)
        target_sr = duel.current_star_rating + duel.pressure_offset

        played = (await session.execute(
            select(BskDuelRound.beatmap_id).where(BskDuelRound.duel_id == duel_id)
        )).scalars().all()

        # Build TWO per-player pools — each guarantees 1 map per component
        # (aim/speed/acc/cons) + 2 random fillers, and the two pools share no maps.
        p1_pool = await get_balanced_pick_candidates(target_sr, exclude_ids=list(played))
        p2_pool = await get_balanced_pick_candidates(
            target_sr,
            exclude_ids=list(played) + [m.beatmap_id for m in p1_pool],
        )
        if not p1_pool or not p2_pool:
            logger.warning(f"_start_pick_phase: no candidates for duel {duel_id}, skipping pick")
            await _start_next_round(bot, duel_id, osu_api)
            return
        p1_name    = p1.osu_username    if p1      else 'Player 1'
        p2_name    = p2_user.osu_username if p2_user else 'Player 2'
        p1_country = (p1.country      or '') if p1      else ''
        p2_country = (p2_user.country or '') if p2_user else ''
        p1_tg_id   = p1.telegram_id       if p1      else None
        p2_tg_id   = p2_user.telegram_id  if p2_user else None
        p1_cover   = (p1.cover_url      or '') if p1      else ''
        p2_cover   = (p2_user.cover_url or '') if p2_user else ''

        duel.pick_candidates_p1 = ",".join(str(m.beatmap_id) for m in p1_pool)
        duel.pick_candidates_p2 = ",".join(str(m.beatmap_id) for m in p2_pool)
        # Legacy mirror — points at the active picker's pool, set in _resolve_ban.
        duel.pick_candidates = duel.pick_candidates_p1
        duel.pick_p1 = None
        duel.pick_p2 = None
        duel.pick_turn = None
        duel.pick_played = None
        await session.commit()

    is_test   = duel.is_test
    round_num = duel.current_round + 1
    test_tag  = ' [TEST]' if is_test else ''

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

    dm_candidates_p1   = [_to_dm(m) for m in p1_pool]
    dm_candidates_p2   = [_to_dm(m) for m in p2_pool]
    group_candidates_p1 = [{'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''} for m in p1_pool]
    group_candidates_p2 = [{'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''} for m in p2_pool]

    p1_mu_global = r1.mu_global if r1 else 250.0
    p2_mu_global = r2.mu_global if r2 else 250.0
    # R1 picker = lower-mu player. For later re-ban rounds (ranked: 4/8/12/16),
    # preserve alternation so the same player doesn't pick twice in a row when
    # the ban phase resets pick_turn.
    r1_first_is_p1 = p1_mu_global <= p2_mu_global
    if round_num % 2 == 1:
        p1_priority = r1_first_is_p1
    else:
        p1_priority = not r1_first_is_p1

    from services.image import card_renderer

    # ── 1. Group chat — face-down pool card, no inline keyboard ──────────────
    group_card_data = {
        'round_number': round_num,
        'p1_name':      p1_name,
        'p2_name':      p2_name,
        'p1_country':   p1_country,
        'p2_country':   p2_country,
        'phase':        'ban',
        'p1_ready':     False,
        'p2_ready':     False,
        'p1_picked':    None,
        'p2_picked':    None,
        'candidates':   group_candidates_p1 + group_candidates_p2,
        'banned_ids':   [],
    }
    group_img = await card_renderer.generate_bsk_pool_group_card_async(group_card_data)
    group_caption = (
        f"🗳 <b>Раунд {round_num} — Фаза бана{test_tag}</b>\n"
        f"Карты отправлены в личку. ⏳ {BAN_TIMEOUT_SECONDS} сек на бан"
    )
    try:
        new_mid = await _send_or_edit_photo(
            bot, duel.chat_id, duel.message_id,
            group_img, caption=group_caption,
            thread_id=duel.message_thread_id,
        )
        if new_mid != duel.message_id:
            async with get_db_session() as _s:
                _d = (await _s.execute(
                    select(BskDuel).where(BskDuel.id == duel_id)
                )).scalar_one_or_none()
                if _d:
                    _d.message_id = new_mid
                    await _s.commit()
    except Exception as e:
        logger.error(f"_start_pick_phase: failed to send group card: {e}")

    # ── 2. Wait 5 seconds so players can see the pool ─────────────────────────
    await asyncio.sleep(5)

    # ── 3. Init ban state and send DM ban cards ───────────────────────────────
    # In ban phase, each player bans cards from the OPPONENT's pool (variant C).
    _ban_state[duel_id] = {
        'p1_tg_id':        p1_tg_id,
        'p2_tg_id':        p2_tg_id,
        'p1_dm_msg':       None,
        'p2_dm_msg':       None,
        'p1_bans':         [],            # bans p1 applies → removed from p2's pool
        'p2_bans':         [],            # bans p2 applies → removed from p1's pool
        'p1_ready':        False,
        'p2_ready':        False,
        'dm_candidates_p1':    dm_candidates_p1,    # p1's own pool (for pick phase)
        'dm_candidates_p2':    dm_candidates_p2,    # p2's own pool
        'group_candidates_p1': group_candidates_p1,
        'group_candidates_p2': group_candidates_p2,
        'round_num':       round_num,
        'p1_name':         p1_name,
        'p2_name':         p2_name,
        'p1_country':      p1_country,
        'p2_country':      p2_country,
        'p1_priority':     p1_priority,
        'is_test':         is_test,
        'p1_cover_url':    p1_cover,
        'p2_cover_url':    p2_cover,
    }
    state = _ban_state[duel_id]

    async def _send_ban_dm(tg_id: int, player_name: str, player_country: str,
                           cover_url: str, opponent_pool: list,
                           opponent_name: str) -> Optional[int]:
        # Variant C: each player bans from the OPPONENT's pool.
        dm_data = {
            'round_number':    round_num,
            'player_name':     player_name,
            'player_country':  player_country,
            'player_cover_url': cover_url or None,
            'phase':           'ban',
            'priority':        False,
            'banned_ids':      [],
            'ban_count':       0,
            'max_bans':        MAX_BANS,
            'candidates':      opponent_pool,
        }
        img = await card_renderer.generate_bsk_pool_dm_card_async(dm_data)
        img.seek(0)
        kb = _ban_keyboard(duel_id, opponent_pool, [])
        caption = (
            f"🚫 <b>Раунд {round_num} · Фаза бана{test_tag}</b>\n"
            f"Это пул <b>{escape_html(opponent_name)}</b> — выбери до {MAX_BANS} карт для бана.\n"
            f"⏳ {BAN_TIMEOUT_SECONDS} сек\n\n"
            f"{_format_pick_pool_links(opponent_pool)}"
        )
        try:
            msg = await bot.send_photo(
                tg_id,
                photo=BufferedInputFile(img.read(), filename='ban_pool.png'),
                caption=caption,
                parse_mode='HTML',
                reply_markup=kb,
            )
            return msg.message_id
        except Exception as exc:
            logger.warning(f"_start_pick_phase: ban DM to tg_id={tg_id} failed — {exc}")
            return None

    if p1_tg_id:
        # p1 bans from p2's pool
        state['p1_dm_msg'] = await _send_ban_dm(
            p1_tg_id, p1_name, p1_country, p1_cover,
            opponent_pool=dm_candidates_p2, opponent_name=p2_name,
        )
    if p2_tg_id and p2_tg_id != p1_tg_id:
        # p2 bans from p1's pool
        state['p2_dm_msg'] = await _send_ban_dm(
            p2_tg_id, p2_name, p2_country, p2_cover,
            opponent_pool=dm_candidates_p1, opponent_name=p1_name,
        )

    # ── 4. Schedule ban expiry ────────────────────────────────────────────────
    asyncio.create_task(_expire_ban(bot, duel_id, osu_api, BAN_TIMEOUT_SECONDS))


# ─────────────────────────────────────────────────────────────────────────────
# BAN PHASE — toggle / confirm / resolve
# ─────────────────────────────────────────────────────────────────────────────

async def toggle_ban(bot: Bot, duel_id: int, tg_user_id: int, beatmap_id: int) -> str:
    """
    Toggle a map in a player's ban selection.
    Returns 'ok' | 'limit' | 'invalid' | 'already_ready'.
    """
    state = _ban_state.get(duel_id)
    if not state:
        return 'invalid'

    p1_tg_id = state.get('p1_tg_id')
    p2_tg_id = state.get('p2_tg_id')

    # Variant C: p1 bans from p2's pool, p2 bans from p1's.
    if tg_user_id == p1_tg_id:
        bans_key   = 'p1_bans'
        dm_msg_key = 'p1_dm_msg'
        ready_key  = 'p1_ready'
        opp_pool_key = 'dm_candidates_p2'
    elif tg_user_id == p2_tg_id:
        bans_key   = 'p2_bans'
        dm_msg_key = 'p2_dm_msg'
        ready_key  = 'p2_ready'
        opp_pool_key = 'dm_candidates_p1'
    else:
        return 'invalid'

    if state.get(ready_key):
        return 'already_ready'

    opp_pool = state.get(opp_pool_key, [])
    valid_ids = {m['beatmap_id'] for m in opp_pool}
    if beatmap_id not in valid_ids:
        return 'invalid'

    bans = state[bans_key]
    if beatmap_id in bans:
        bans.remove(beatmap_id)
    else:
        if len(bans) >= MAX_BANS:
            return 'limit'
        bans.append(beatmap_id)

    # Re-render keyboard with updated toggle state
    dm_msg = state.get(dm_msg_key)
    if dm_msg:
        kb = _ban_keyboard(duel_id, opp_pool, bans)
        await safe_edit_reply_markup(
            bot,
            chat_id=tg_user_id,
            message_id=dm_msg,
            reply_markup=kb,
        )

    return 'ok'


async def confirm_ban(bot: Bot, duel_id: int, tg_user_id: int) -> str:
    """
    Confirm bans for a player. If both players have confirmed, resolves immediately.
    Returns 'ok' | 'done' | 'invalid' | 'already'.
    """
    state = _ban_state.get(duel_id)
    if not state:
        return 'invalid'

    p1_tg_id = state.get('p1_tg_id')
    p2_tg_id = state.get('p2_tg_id')
    is_test  = state.get('is_test', False)

    if tg_user_id == p1_tg_id:
        ready_key  = 'p1_ready'
        dm_msg_key = 'p1_dm_msg'
    elif tg_user_id == p2_tg_id:
        ready_key  = 'p2_ready'
        dm_msg_key = 'p2_dm_msg'
    else:
        return 'invalid'

    if state.get(ready_key):
        return 'already'
    state[ready_key] = True

    # Remove keyboard from this player's DM
    dm_msg = state.get(dm_msg_key)
    if dm_msg:
        await safe_edit_caption(
            bot,
            chat_id=tg_user_id,
            message_id=dm_msg,
            caption="✅ <b>Баны подтверждены!</b> Ждём соперника…",
            parse_mode="HTML",
            reply_markup=None,
        )

    # Test duel: one confirmation counts for both
    if is_test and p1_tg_id == p2_tg_id:
        state['p1_ready'] = True
        state['p2_ready'] = True

    # Update group card to reflect readiness
    await _update_ban_group_card(bot, duel_id, state)

    if state.get('p1_ready') and state.get('p2_ready'):
        from services.bsk.duel_manager import _osu_api
        asyncio.create_task(_resolve_ban(bot, duel_id, _osu_api))
        return 'done'

    return 'ok'


async def _update_ban_group_card(bot: Bot, duel_id: int, state: dict) -> None:
    """Re-render group card to show updated ban phase status."""
    from services.image import card_renderer
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            return
        chat_id    = duel.chat_id
        message_id = duel.message_id
        thread_id  = duel.message_thread_id

    all_bans = list(set(state.get('p1_bans', [])) | set(state.get('p2_bans', [])))
    group_card_data = {
        'round_number': state.get('round_num', 1),
        'p1_name':      state.get('p1_name', 'P1'),
        'p2_name':      state.get('p2_name', 'P2'),
        'p1_country':   state.get('p1_country', ''),
        'p2_country':   state.get('p2_country', ''),
        'phase':        'ban',
        'p1_ready':     state.get('p1_ready', False),
        'p2_ready':     state.get('p2_ready', False),
        'p1_picked':    None,
        'p2_picked':    None,
        # Show union of both pools (renderer treats this as a flat candidate list).
        'candidates':   state.get('group_candidates_p1', []) + state.get('group_candidates_p2', []),
        'banned_ids':   all_bans,
    }
    try:
        img = await card_renderer.generate_bsk_pool_group_card_async(group_card_data)
        caption = (
            f"🚫 <b>Раунд {state.get('round_num', 1)} — Фаза бана</b>\n"
            f"Игроки выбирают баны…"
        )
        await _send_or_edit_photo(bot, chat_id, message_id, img, caption=caption, thread_id=thread_id)
    except Exception as e:
        logger.warning(f"_update_ban_group_card: {e}")


async def _resolve_ban(bot: Bot, duel_id: int, osu_api) -> None:
    """Apply bans, refill each player's pool to POOL_SIZE, init pool state, send first pick DM.

    Variant C: p1's bans hit p2's pool, p2's bans hit p1's pool.
    """
    import random as _random
    state = _ban_state.pop(duel_id, {})
    if not state:
        return

    p1_bans = set(state.get('p1_bans', []))   # → applied to p2's pool
    p2_bans = set(state.get('p2_bans', []))   # → applied to p1's pool

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or not duel.pick_candidates_p1 or not duel.pick_candidates_p2:
            return

        p1_ids = [int(x) for x in (duel.pick_candidates_p1 or "").split(",") if x]
        p2_ids = [int(x) for x in (duel.pick_candidates_p2 or "").split(",") if x]

        p1_remaining = [bid for bid in p1_ids if bid not in p2_bans]
        p2_remaining = [bid for bid in p2_ids if bid not in p1_bans]

        played_history = (await session.execute(
            select(BskDuelRound.beatmap_id).where(BskDuelRound.duel_id == duel_id)
        )).scalars().all()
        target_sr = duel.current_star_rating + duel.pressure_offset

        async def _refill(remaining: list[int], other_pool: list[int]) -> list[int]:
            n_to_add = max(0, POOL_SIZE - len(remaining))
            if n_to_add == 0:
                return remaining
            exclude = list(played_history) + remaining + other_pool
            extra = await get_pick_candidates(
                target_sr, n=n_to_add * 4, exclude_ids=exclude,
            )
            _random.shuffle(extra)
            return remaining + [m.beatmap_id for m in extra[:n_to_add]]

        new_p1_ids = await _refill(p1_remaining, p2_remaining)
        new_p2_ids = await _refill(p2_remaining, new_p1_ids)

        duel.pick_candidates_p1 = ",".join(str(b) for b in new_p1_ids)
        duel.pick_candidates_p2 = ",".join(str(b) for b in new_p2_ids)
        duel.pick_played = ''
        p1_priority = state.get('p1_priority', True)
        duel.pick_turn = 1 if p1_priority else 2
        # Legacy mirror — point at the active picker's pool
        duel.pick_candidates = duel.pick_candidates_p1 if duel.pick_turn == 1 else duel.pick_candidates_p2
        duel.pick_p1 = None
        duel.pick_p2 = None
        await session.commit()

        # Fetch full rows for both pools
        all_ids = list(set(new_p1_ids) | set(new_p2_ids))
        all_rows = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id.in_(all_ids))
        )).scalars().all()
        map_by_id = {m.beatmap_id: m for m in all_rows}
        new_p1_maps = [map_by_id[b] for b in new_p1_ids if b in map_by_id]
        new_p2_maps = [map_by_id[b] for b in new_p2_ids if b in map_by_id]

    round_num   = state.get('round_num', 1)
    p1_name     = state.get('p1_name', 'P1')
    p2_name     = state.get('p2_name', 'P2')
    p1_country  = state.get('p1_country', '')
    p2_country  = state.get('p2_country', '')
    p1_tg_id    = state.get('p1_tg_id')
    p2_tg_id    = state.get('p2_tg_id')
    is_test     = state.get('is_test', False)

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

    dm_candidates_p1   = [_to_dm(m) for m in new_p1_maps]
    dm_candidates_p2   = [_to_dm(m) for m in new_p2_maps]
    group_candidates_p1 = [{'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''} for m in new_p1_maps]
    group_candidates_p2 = [{'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''} for m in new_p2_maps]

    _pool_state[duel_id] = {
        'p1_tg_id':          p1_tg_id,
        'p2_tg_id':          p2_tg_id,
        'dm_candidates_p1':    dm_candidates_p1,
        'dm_candidates_p2':    dm_candidates_p2,
        'group_candidates_p1': group_candidates_p1,
        'group_candidates_p2': group_candidates_p2,
        'round_num':         round_num,
        'p1_name':           p1_name,
        'p2_name':           p2_name,
        'p1_country':        p1_country,
        'p2_country':        p2_country,
        'p1_cover_url':      state.get('p1_cover_url') or '',
        'p2_cover_url':      state.get('p2_cover_url') or '',
        'is_test':           is_test,
        'active_pick_dm_msg': None,
        'active_pick_tg_id':  None,
    }

    await _send_pick_to_active_player(bot, duel_id, osu_api)


async def _expire_ban(bot: Bot, duel_id: int, osu_api, delay: int) -> None:
    """Auto-resolve ban phase after `delay` seconds if still pending."""
    await asyncio.sleep(delay)
    if duel_id not in _ban_state:
        return  # already resolved by players
    logger.info(f"_expire_ban: auto-resolving ban for duel {duel_id}")
    state = _ban_state.get(duel_id)
    if state:
        state['p1_ready'] = True
        state['p2_ready'] = True
    await _resolve_ban(bot, duel_id, osu_api)


async def _send_pick_to_active_player(bot: Bot, duel_id: int, osu_api) -> None:
    """Send pick DM to the player whose turn it is. Update group card."""
    from services.image import card_renderer

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.pick_turn is None:
            return
        pick_turn = duel.pick_turn
        active_pool_str = duel.pick_candidates_p1 if pick_turn == 1 else duel.pick_candidates_p2
        if not active_pool_str:
            return
        candidates_str = active_pool_str
        # Keep legacy mirror in sync so other code paths see the active pool.
        if duel.pick_candidates != active_pool_str:
            duel.pick_candidates = active_pool_str
            await session.commit()
        chat_id       = duel.chat_id
        message_id    = duel.message_id
        thread_id     = duel.message_thread_id
        round_num     = duel.current_round + 1

    pool_st = _pool_state.get(duel_id)
    if not pool_st:
        return

    candidate_ids = [int(x) for x in candidates_str.split(",") if x]
    available_ids = set(candidate_ids)

    # Active player's own pool — they pick from their own maps.
    dm_candidates    = pool_st['dm_candidates_p1' if pick_turn == 1 else 'dm_candidates_p2']
    group_candidates = pool_st['group_candidates_p1' if pick_turn == 1 else 'group_candidates_p2']
    p1_name     = pool_st['p1_name']
    p2_name     = pool_st['p2_name']
    p1_country  = pool_st['p1_country']
    p2_country  = pool_st['p2_country']
    p1_tg_id    = pool_st['p1_tg_id']
    p2_tg_id    = pool_st['p2_tg_id']
    is_test     = pool_st.get('is_test', False)
    test_tag    = ' [TEST]' if is_test else ''

    active_tg_id   = p1_tg_id if pick_turn == 1 else p2_tg_id
    active_name    = p1_name if pick_turn == 1 else p2_name
    active_country = p1_country if pick_turn == 1 else p2_country
    active_cover   = (pool_st.get('p1_cover_url') if pick_turn == 1
                      else pool_st.get('p2_cover_url')) or ''

    if is_test and p1_tg_id == p2_tg_id:
        active_tg_id = p1_tg_id

    # Update group card — show pick phase + whose turn
    group_card_data = {
        'round_number':   round_num,
        'p1_name':        p1_name,
        'p2_name':        p2_name,
        'p1_country':     p1_country,
        'p2_country':     p2_country,
        'phase':          'pick',
        'p1_ready':       False,
        'p2_ready':       False,
        'p1_picked':      None,
        'p2_picked':      None,
        'candidates':     group_candidates,
        'banned_ids':     [],
        'played_ids':     [],
        'pick_turn_name': active_name,
    }
    try:
        group_img = await card_renderer.generate_bsk_pool_group_card_async(group_card_data)
        caption = (
            f"🗳 <b>Раунд {round_num} — Выбор карты{test_tag}</b>\n"
            f"Очередь: <b>{escape_html(active_name)}</b>. ⏳ {PICK_TIMEOUT_SECONDS} сек"
        )
        new_mid = await _send_or_edit_photo(bot, chat_id, message_id, group_img, caption=caption, thread_id=thread_id)
        if new_mid != message_id:
            async with get_db_session() as _s:
                _d = (await _s.execute(select(BskDuel).where(BskDuel.id == duel_id))).scalar_one_or_none()
                if _d:
                    _d.message_id = new_mid
                    await _s.commit()
    except Exception as e:
        logger.error(f"_send_pick_to_active_player: group card failed: {e}")

    # Send DM to active picker
    dm_msg_id = None
    if active_tg_id:
        dm_data = {
            'round_number':    round_num,
            'player_name':     active_name,
            'player_country':  active_country,
            'player_cover_url': active_cover or None,
            'phase':           'pick',
            'priority':        True,
            'banned_ids':      [],
            'ban_count':       0,
            'max_bans':        0,
            'candidates':      dm_candidates,
            'played_ids':      [],
        }
        kb = _pick_keyboard(duel_id, dm_candidates, available_ids)
        try:
            img = await card_renderer.generate_bsk_pool_dm_card_async(dm_data)
            img.seek(0)
            dm_caption = (
                f"🗳 <b>Твоя очередь выбирать карту{test_tag}</b>\n"
                f"⏳ {PICK_TIMEOUT_SECONDS} сек\n\n"
                f"{_format_pick_pool_links(dm_candidates, available_ids)}"
            )
            msg = await bot.send_photo(
                active_tg_id,
                photo=BufferedInputFile(img.read(), filename='pool.png'),
                caption=dm_caption,
                parse_mode='HTML',
                reply_markup=kb,
            )
            dm_msg_id = msg.message_id
        except Exception as exc:
            logger.warning(f"_send_pick_to_active_player: DM failed — {exc}")

    pool_st['active_pick_dm_msg'] = dm_msg_id
    pool_st['active_pick_tg_id']  = active_tg_id

    asyncio.create_task(
        _expire_single_pick(bot, duel_id, osu_api, PICK_TIMEOUT_SECONDS, candidates_str, pick_turn)
    )


async def submit_pick(bot: Bot, duel_id: int, user_id: int, beatmap_id: int) -> str:
    """
    Register active player's pick. Returns:
      'done'          — pick accepted, round starting
      'invalid'       — not in pick phase or bad map
      'not_your_turn' — it's the other player's turn
      'already'       — pick already submitted
    """
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()

        if not duel or duel.pick_turn is None:
            return 'invalid'
        if duel.status not in ('accepted', 'round_active'):
            return 'invalid'

        # The active player picks from their OWN pool only.
        active_pool_str = duel.pick_candidates_p1 if duel.pick_turn == 1 else duel.pick_candidates_p2
        if not active_pool_str:
            return 'invalid'
        candidate_ids = [int(x) for x in active_pool_str.split(",") if x]
        if beatmap_id not in candidate_ids:
            return 'invalid'

        # Determine player number
        if duel.is_test and duel.player1_user_id == duel.player2_user_id:
            player_num = duel.pick_turn
        elif user_id == duel.player1_user_id:
            player_num = 1
        elif user_id == duel.player2_user_id:
            player_num = 2
        else:
            return 'invalid'

        if duel.pick_turn != player_num:
            return 'not_your_turn'

        # Atomic CAS: only set pick_pX if it's still NULL.  Prevents two concurrent
        # submit_pick calls (e.g. double-tap) from both passing the "already" check
        # and triggering _resolve_single_pick twice.
        if player_num == 1:
            result = await session.execute(
                sa_update(BskDuel)
                .where(
                    BskDuel.id == duel_id,
                    BskDuel.pick_turn == 1,
                    BskDuel.pick_p1.is_(None),
                )
                .values(pick_p1=beatmap_id)
            )
        else:
            result = await session.execute(
                sa_update(BskDuel)
                .where(
                    BskDuel.id == duel_id,
                    BskDuel.pick_turn == 2,
                    BskDuel.pick_p2.is_(None),
                )
                .values(pick_p2=beatmap_id)
            )
        if result.rowcount == 0:
            return 'already'
        await session.commit()

    # Remove DM keyboard from the player who just picked
    pool_st = _pool_state.get(duel_id, {})
    dm_msg = pool_st.get('active_pick_dm_msg')
    tg_id  = pool_st.get('active_pick_tg_id')
    if dm_msg and tg_id:
        await safe_edit_caption(
            bot,
            chat_id=tg_id,
            message_id=dm_msg,
            caption="✅ <b>Выбор принят!</b> Начинаем раунд…",
            parse_mode="HTML",
            reply_markup=None,
        )
    # Clear stale active-pick handles so the next turn's edits don't target this DM
    if pool_st:
        pool_st['active_pick_dm_msg'] = None
        pool_st['active_pick_tg_id']  = None

    from services.bsk.duel_manager import _osu_api
    await _resolve_single_pick(bot, duel_id, osu_api=_osu_api)
    return 'done'


async def _resolve_single_pick(bot: Bot, duel_id: int, osu_api) -> None:
    """Consume the active player's pick, refill pool with one fresh map,
    rebuild in-memory dm/group candidates, then start the round."""
    from services.bsk.duel_round import _start_next_round
    import random as _random

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            return

        pick_turn = duel.pick_turn or 1
        chosen_id = duel.pick_p1 if pick_turn == 1 else duel.pick_p2

        active_pool_str = duel.pick_candidates_p1 if pick_turn == 1 else duel.pick_candidates_p2
        other_pool_str  = duel.pick_candidates_p2 if pick_turn == 1 else duel.pick_candidates_p1
        if not active_pool_str:
            return
        candidate_ids = [int(x) for x in active_pool_str.split(",") if x]
        other_ids     = [int(x) for x in (other_pool_str or "").split(",") if x]

        if not chosen_id:
            # Gameplay tie-breaker, not cryptography — random.choice is fine here.
            chosen_id = _random.choice(candidate_ids) if candidate_ids else None  # nosec B311
        if not chosen_id:
            await _start_next_round(bot, duel_id, osu_api)
            return

        # Append to played history (used as exclusion list when refilling both pools).
        played_str = duel.pick_played or ''
        duel.pick_played = f"{played_str},{chosen_id}" if played_str else str(chosen_id)

        kept_ids = [bid for bid in candidate_ids if bid != chosen_id]

        played_history = (await session.execute(
            select(BskDuelRound.beatmap_id).where(BskDuelRound.duel_id == duel_id)
        )).scalars().all()
        target_sr = duel.current_star_rating + duel.pressure_offset

        # Refill ONLY the active player's pool — the other player's pool is unchanged.
        n_to_add  = max(0, POOL_SIZE - len(kept_ids))
        new_maps_for_pool: list[BskMapPool] = []
        if n_to_add > 0:
            extra = await get_pick_candidates(
                target_sr, n=n_to_add * 4,
                exclude_ids=list(played_history) + [chosen_id] + kept_ids + other_ids,
            )
            _random.shuffle(extra)
            new_maps_for_pool = extra[:n_to_add]

        new_pool_ids = kept_ids + [m.beatmap_id for m in new_maps_for_pool]
        if pick_turn == 1:
            duel.pick_candidates_p1 = ",".join(str(b) for b in new_pool_ids) or None
        else:
            duel.pick_candidates_p2 = ",".join(str(b) for b in new_pool_ids) or None

        # Switch turn for the next pick. Update legacy mirror to match new active pool.
        new_turn = 2 if pick_turn == 1 else 1
        duel.pick_turn = new_turn
        new_active_str = duel.pick_candidates_p1 if new_turn == 1 else duel.pick_candidates_p2
        duel.pick_candidates = new_active_str
        duel.pick_p1 = None
        duel.pick_p2 = None
        await session.commit()

        # Fetch full pool rows (preserve order) for the in-memory caches
        pool_rows = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id.in_(new_pool_ids))
        )).scalars().all() if new_pool_ids else []
        rows_by_id = {m.beatmap_id: m for m in pool_rows}
        ordered_pool = [rows_by_id[b] for b in new_pool_ids if b in rows_by_id]

        beatmap = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id == chosen_id)
        )).scalar_one_or_none()

    # Refresh ONLY the active (just-picked) player's in-memory cache; opponent's stays.
    pool_st = _pool_state.get(duel_id)
    if pool_st is not None and ordered_pool:
        dm_key    = 'dm_candidates_p1'    if pick_turn == 1 else 'dm_candidates_p2'
        group_key = 'group_candidates_p1' if pick_turn == 1 else 'group_candidates_p2'
        pool_st[dm_key] = [
            {
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
            } for m in ordered_pool
        ]
        pool_st[group_key] = [
            {'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''}
            for m in ordered_pool
        ]

    await _start_next_round(bot, duel_id, osu_api, forced_map=beatmap)


async def _expire_single_pick(bot: Bot, duel_id: int, osu_api, delay: int,
                               expected_candidates: str, expected_turn: int) -> None:
    """Auto-pick after timeout for a single player's turn."""
    await asyncio.sleep(delay)

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            return
        active_pool = duel.pick_candidates_p1 if expected_turn == 1 else duel.pick_candidates_p2
        if not active_pool:
            return
        if active_pool != expected_candidates:
            return
        if duel.pick_turn != expected_turn:
            return
        pick_field = duel.pick_p1 if expected_turn == 1 else duel.pick_p2
        if pick_field is not None:
            return

    # Remove DM keyboard from timed-out player
    pool_st = _pool_state.get(duel_id, {})
    dm_msg = pool_st.get('active_pick_dm_msg')
    tg_id  = pool_st.get('active_pick_tg_id')
    if dm_msg and tg_id:
        await safe_edit_caption(
            bot,
            chat_id=tg_id,
            message_id=dm_msg,
            caption="⏰ <b>Время вышло!</b> Карта выбрана случайно из доступных.",
            parse_mode="HTML",
            reply_markup=None,
        )
    if pool_st:
        pool_st['active_pick_dm_msg'] = None
        pool_st['active_pick_tg_id']  = None

    logger.info(f"_expire_single_pick: auto-picking for duel {duel_id} turn {expected_turn}")
    await _resolve_single_pick(bot, duel_id, osu_api)
