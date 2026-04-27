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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, InputMediaPhoto
from sqlalchemy import select, update as sa_update

from db.database import get_db_session
from db.models.bsk_duel import BskDuel
from db.models.bsk_duel_round import BskDuelRound
from db.models.bsk_map_pool import BskMapPool
from db.models.user import User
from services.bsk.composite import composite_score, composite_points
from services.bsk.ml_inference import predict_round_winner
from services.bsk.map_selector import get_map_for_round, get_pick_candidates, next_star_rating
from services.bsk.rating import update_ratings
from utils.logger import get_logger

logger = get_logger("bsk.duel_manager")

_osu_api = None
_bot: Optional[Bot] = None

# ── In-memory pick-phase DM state ─────────────────────────────────────────────
# Keyed by duel_id.  Cleared when pick resolves or duel is cancelled.
# Structure:
#   p1_tg_id   int|None   — telegram user ID of player 1
#   p2_tg_id   int|None   — telegram user ID of player 2
#   p1_dm_msg  int|None   — message_id of the DM card sent to P1
#   p2_dm_msg  int|None   — message_id of the DM card sent to P2
#   candidates list[dict] — full candidate data for group-card re-renders
#   round_num  int
#   p1_name, p2_name, p1_country, p2_country  str
#   p1_priority bool      — True: P1 has pick priority (lower BSK rating)
_pick_dm_state: dict[int, dict] = {}

# ── In-memory ban-phase state ─────────────────────────────────────────────────
# Keyed by duel_id.  Cleared when bans resolve or on cancel.
# Structure:
#   p1_tg_id, p2_tg_id     int|None  — telegram IDs
#   p1_dm_msg, p2_dm_msg   int|None  — message IDs of ban DM cards
#   p1_bans, p2_bans        list[int] — beatmap_ids selected for ban
#   p1_ready, p2_ready      bool
#   dm_candidates          list[dict] — full map data (for DM card renders)
#   group_candidates       list[dict] — thin data for group card
#   round_num, p1_name, p2_name, p1_country, p2_country  str/int
#   p1_priority            bool
#   is_test                bool
_ban_state: dict[int, dict] = {}

BAN_TIMEOUT_SECONDS = 60
MAX_BANS = 3


async def _send_or_edit_photo(
    bot: Bot,
    chat_id: int,
    message_id: Optional[int],
    img_bytes,
    caption: str = "",
    reply_markup=None,
) -> Optional[int]:
    """
    Send a new photo message (if message_id is None) or edit the existing one.
    Returns the new message_id (may be different from input if a new message was sent).
    """
    from io import BytesIO
    if isinstance(img_bytes, BytesIO):
        img_bytes.seek(0)
        raw = img_bytes.read()
    else:
        raw = img_bytes

    file = BufferedInputFile(raw, filename="duel.png")

    if message_id is None:
        msg = await bot.send_photo(
            chat_id,
            photo=file,
            caption=caption or None,
            parse_mode="HTML" if caption else None,
            reply_markup=reply_markup,
        )
        return msg.message_id
    else:
        try:
            await bot.edit_message_media(
                media=InputMediaPhoto(
                    media=file,
                    caption=caption or None,
                    parse_mode="HTML" if caption else None,
                ),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning(f"_send_or_edit_photo edit failed ({e}), sending new message")
            msg = await bot.send_photo(
                chat_id,
                photo=BufferedInputFile(raw, filename="duel.png"),
                caption=caption or None,
                parse_mode="HTML" if caption else None,
                reply_markup=reply_markup,
            )
            return msg.message_id
        return message_id


def init_duel_manager(bot: Bot, osu_api) -> None:
    global _osu_api, _bot
    _bot = bot
    _osu_api = osu_api

ACCEPT_TIMEOUT_MINUTES = 5
SCORE_POLL_INTERVAL = 15  # seconds
TARGET_SCORE = 1_000_000


def _forfeit_deadline(map_length_seconds: int) -> datetime:
    buffer = 15 * 60  # 15 min buffer
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
            total_rounds=0,
            target_score=TARGET_SCORE,
            expires_at=expires_at,
            version=2,
        )
        session.add(duel)
        await session.commit()
        await session.refresh(duel)

        msg = await bot.send_message(
            chat_id,
            f"⚔️ <b>ВЫЗОВ НА ДУЭЛЬ</b>\n\n"
            f"<b>{challenger.osu_username}</b> бросает вызов <b>{opponent.osu_username}</b>!\n\n"
            f"🎮 Режим: <b>{mode.upper()}</b>\n"
            f"🏁 Цель: <b>{TARGET_SCORE:,} pts</b>\n"
            f"⏳ Время на принятие: <b>{ACCEPT_TIMEOUT_MINUTES} мин</b>\n\n"
            f"<i>{opponent.osu_username}, принимаешь вызов?</i>",
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
        await session.commit()

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
        name = p2.osu_username if p2 else "Игрок"

        try:
            await bot.edit_message_text(
                f"❌ <b>{name}</b> отклонил вызов.\n\n"
                f"<i>Дуэль отменена.</i>",
                chat_id=duel.chat_id,
                message_id=duel.message_id,
                parse_mode="HTML",
            )
        except Exception:
            pass

    return True


PICK_TIMEOUT_SECONDS = 60


def _base_sr_for_duel(r1, r2) -> float:
    """
    Compute the base star-rating for a duel from the two players' ratings.
    Uses the SUM of the four components (not mu_global weighted mean) because
    starting_mu_from_pp() was designed on the sum scale:
        sum = mu_aim + mu_speed + mu_acc + mu_cons
        SR  = sum / 200  (e.g. sum=1000 → 5.0★, sum=800 → 4.0★)
    """
    sum1 = r1.mu_aim + r1.mu_speed + r1.mu_acc + r1.mu_cons
    sum2 = r2.mu_aim + r2.mu_speed + r2.mu_acc + r2.mu_cons
    sr = round((sum1 + sum2) / 2 / 200, 1)
    return max(1.0, min(10.0, sr))


def _pick_keyboard(duel_id: int, candidates: list) -> InlineKeyboardMarkup:
    """Two rows of 3 number buttons matching the 3×2 pick grid."""
    row1, row2 = [], []
    for i, m in enumerate(candidates):
        bid = m.beatmap_id if hasattr(m, 'beatmap_id') else m.get('beatmap_id')
        btn = InlineKeyboardButton(
            text=str(i + 1),
            callback_data=f"bskpick:{duel_id}:{bid}",
        )
        (row1 if i < 3 else row2).append(btn)
    return InlineKeyboardMarkup(inline_keyboard=[b for b in [row1, row2] if b])


def _ban_keyboard(duel_id: int, candidates: list, user_bans: list) -> InlineKeyboardMarkup:
    """Ban phase keyboard: 6 toggle buttons (2 rows of 3) + confirm/replace row."""
    rows = []
    for i in range(0, len(candidates), 3):
        chunk = candidates[i:i + 3]
        row = []
        for m in chunk:
            bid = m.get('beatmap_id') if isinstance(m, dict) else m.beatmap_id
            selected = bid in user_bans
            title = (m.get('title') if isinstance(m, dict) else m.title) or 'Map'
            row.append(InlineKeyboardButton(
                text=('✕ ' if selected else '') + title[:15],
                callback_data=f"bskban:{duel_id}:{bid}",
            ))
        rows.append(row)
    ban_count = len(user_bans)
    if ban_count >= MAX_BANS:
        confirm_label = f"✓ Confirm ({ban_count}/{MAX_BANS} bans)"
    elif ban_count > 0:
        confirm_label = f"Replace — +{ban_count} random"
    else:
        confirm_label = "Skip bans"
    rows.append([InlineKeyboardButton(
        text=confirm_label,
        callback_data=f"bskbandone:{duel_id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _start_pick_phase(bot: Bot, duel_id: int, osu_api) -> None:
    """Select 6 candidate maps, display the pool card, then start the ban phase."""
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status not in ('accepted', 'round_active'):
            return

        if max(duel.player1_total_score, duel.player2_total_score) >= duel.target_score:
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
            duel.current_star_rating = _base_sr_for_duel(r1, r2)
        target_sr = duel.current_star_rating + duel.pressure_offset

        played = (await session.execute(
            select(BskDuelRound.beatmap_id).where(BskDuelRound.duel_id == duel_id)
        )).scalars().all()

        candidates = await get_pick_candidates(target_sr, n=6, exclude_ids=list(played))
        if not candidates:
            logger.warning(f"_start_pick_phase: no candidates for duel {duel_id}, skipping pick")
            await _start_next_round(bot, duel_id, osu_api)
            return

        p1_name    = p1.osu_username    if p1      else 'Player 1'
        p2_name    = p2_user.osu_username if p2_user else 'Player 2'
        p1_country = (p1.country      or '') if p1      else ''
        p2_country = (p2_user.country or '') if p2_user else ''
        p1_tg_id   = p1.telegram_id       if p1      else None
        p2_tg_id   = p2_user.telegram_id  if p2_user else None

        duel.pick_candidates = ",".join(str(m.beatmap_id) for m in candidates)
        duel.pick_p1 = None
        duel.pick_p2 = None
        await session.commit()

    is_test   = duel.is_test
    round_num = duel.current_round + 1
    test_tag  = ' [TEST]' if is_test else ''

    dm_candidates = [
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
            'hp':            None,
            'bpm':           m.bpm,
            'drain_time':    m.length,
        }
        for m in candidates
    ]
    group_candidates = [
        {'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''}
        for m in candidates
    ]

    p1_mu_global = r1.mu_global if r1 else 250.0
    p2_mu_global = r2.mu_global if r2 else 250.0
    p1_priority  = p1_mu_global <= p2_mu_global

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
        'candidates':   group_candidates,
        'banned_ids':   [],
    }
    group_img = await card_renderer.generate_bsk_pool_group_card_async(group_card_data)
    group_caption = (
        f"🗳 <b>Round {round_num} — Ban Phase{test_tag}</b>\n"
        f"Cards sent to players privately. ⏳ {BAN_TIMEOUT_SECONDS}s to ban"
    )
    try:
        new_mid = await _send_or_edit_photo(
            bot, duel.chat_id, duel.message_id,
            group_img, caption=group_caption,
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
    _ban_state[duel_id] = {
        'p1_tg_id':        p1_tg_id,
        'p2_tg_id':        p2_tg_id,
        'p1_dm_msg':       None,
        'p2_dm_msg':       None,
        'p1_bans':         [],
        'p2_bans':         [],
        'p1_ready':        False,
        'p2_ready':        False,
        'dm_candidates':   dm_candidates,
        'group_candidates': group_candidates,
        'round_num':       round_num,
        'p1_name':         p1_name,
        'p2_name':         p2_name,
        'p1_country':      p1_country,
        'p2_country':      p2_country,
        'p1_priority':     p1_priority,
        'is_test':         is_test,
    }
    state = _ban_state[duel_id]

    ban_caption = (
        f"🚫 <b>Round {round_num} · Ban Phase{test_tag}</b>\n"
        f"Select up to {MAX_BANS} maps to ban. ⏳ {BAN_TIMEOUT_SECONDS}s"
    )

    async def _send_ban_dm(tg_id: int, player_name: str, player_country: str) -> Optional[int]:
        dm_data = {
            'round_number':   round_num,
            'player_name':    player_name,
            'player_country': player_country,
            'phase':          'ban',
            'priority':       False,
            'banned_ids':     [],
            'ban_count':      0,
            'max_bans':       MAX_BANS,
            'candidates':     dm_candidates,
        }
        img = await card_renderer.generate_bsk_pool_dm_card_async(dm_data)
        img.seek(0)
        kb = _ban_keyboard(duel_id, dm_candidates, [])
        try:
            msg = await bot.send_photo(
                tg_id,
                photo=BufferedInputFile(img.read(), filename='ban_pool.png'),
                caption=ban_caption,
                parse_mode='HTML',
                reply_markup=kb,
            )
            return msg.message_id
        except Exception as exc:
            logger.warning(f"_start_pick_phase: ban DM to tg_id={tg_id} failed — {exc}")
            return None

    if p1_tg_id:
        state['p1_dm_msg'] = await _send_ban_dm(p1_tg_id, p1_name, p1_country)
    if p2_tg_id and p2_tg_id != p1_tg_id:
        state['p2_dm_msg'] = await _send_ban_dm(p2_tg_id, p2_name, p2_country)

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

    if tg_user_id == p1_tg_id:
        bans_key   = 'p1_bans'
        dm_msg_key = 'p1_dm_msg'
        ready_key  = 'p1_ready'
    elif tg_user_id == p2_tg_id:
        bans_key   = 'p2_bans'
        dm_msg_key = 'p2_dm_msg'
        ready_key  = 'p2_ready'
    else:
        return 'invalid'

    if state.get(ready_key):
        return 'already_ready'

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
        kb = _ban_keyboard(duel_id, state.get('dm_candidates', []), bans)
        try:
            await bot.edit_message_reply_markup(
                chat_id=tg_user_id,
                message_id=dm_msg,
                reply_markup=kb,
            )
        except Exception as e:
            logger.debug(f"toggle_ban: keyboard update failed: {e}")

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
        try:
            await bot.edit_message_caption(
                chat_id=tg_user_id,
                message_id=dm_msg,
                caption="✅ <b>Bans confirmed!</b> Waiting for opponent…",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception as e:
            logger.debug(f"confirm_ban: caption edit failed: {e}")

    # Test duel: one confirmation counts for both
    if is_test and p1_tg_id == p2_tg_id:
        state['p1_ready'] = True
        state['p2_ready'] = True

    # Update group card to reflect readiness
    await _update_ban_group_card(bot, duel_id, state)

    if state.get('p1_ready') and state.get('p2_ready'):
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
        'candidates':   state.get('group_candidates', []),
        'banned_ids':   all_bans,
    }
    try:
        img = await card_renderer.generate_bsk_pool_group_card_async(group_card_data)
        caption = (
            f"🚫 <b>Round {state.get('round_num', 1)} — Ban Phase</b>\n"
            f"Players are banning maps…"
        )
        await _send_or_edit_photo(bot, chat_id, message_id, img, caption=caption)
    except Exception as e:
        logger.warning(f"_update_ban_group_card: {e}")


async def _resolve_ban(bot: Bot, duel_id: int, osu_api) -> None:
    """Apply bans, add random replacements, then transition to pick phase."""
    import random as _random

    state = _ban_state.pop(duel_id, {})
    if not state:
        return

    p1_bans  = state.get('p1_bans', [])
    p2_bans  = state.get('p2_bans', [])
    all_bans = set(p1_bans) | set(p2_bans)

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or not duel.pick_candidates:
            return

        candidate_ids = [int(x) for x in duel.pick_candidates.split(",") if x]
        remaining_ids = [bid for bid in candidate_ids if bid not in all_bans]
        n_replace = len(all_bans)

        replacement_maps = []
        if n_replace > 0:
            played = (await session.execute(
                select(BskDuelRound.beatmap_id).where(BskDuelRound.duel_id == duel_id)
            )).scalars().all()
            target_sr = duel.current_star_rating + duel.pressure_offset
            pool = await get_pick_candidates(
                target_sr, n=n_replace * 4,
                exclude_ids=list(played) + candidate_ids,
            )
            _random.shuffle(pool)
            replacement_maps = pool[:n_replace]

        replacement_ids = [m.beatmap_id for m in replacement_maps]
        new_ids = remaining_ids + replacement_ids

        duel.pick_candidates = ",".join(str(bid) for bid in new_ids)
        await session.commit()

        # Fetch BskMapPool objects for all new candidates
        from sqlalchemy import select as _sel
        new_map_rows = (await session.execute(
            _sel(BskMapPool).where(BskMapPool.beatmap_id.in_(new_ids))
        )).scalars().all()
        map_by_id   = {m.beatmap_id: m for m in new_map_rows}
        new_maps    = [map_by_id[bid] for bid in new_ids if bid in map_by_id]

        chat_id    = duel.chat_id
        message_id = duel.message_id

    expected_candidates = duel.pick_candidates

    round_num    = state.get('round_num', 1)
    p1_name      = state.get('p1_name', 'P1')
    p2_name      = state.get('p2_name', 'P2')
    p1_country   = state.get('p1_country', '')
    p2_country   = state.get('p2_country', '')
    p1_priority  = state.get('p1_priority', True)
    p1_tg_id     = state.get('p1_tg_id')
    p2_tg_id     = state.get('p2_tg_id')
    is_test      = state.get('is_test', False)
    test_tag     = ' [TEST]' if is_test else ''

    dm_candidates = [
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
            'hp':            None,
            'bpm':           m.bpm,
            'drain_time':    m.length,
        }
        for m in new_maps
    ]
    group_candidates = [
        {'beatmap_id': m.beatmap_id, 'map_type': m.map_type or ''}
        for m in new_maps
    ]

    from services.image import card_renderer

    # Update group card to pick phase
    group_card_data = {
        'round_number': round_num,
        'p1_name':      p1_name,
        'p2_name':      p2_name,
        'p1_country':   p1_country,
        'p2_country':   p2_country,
        'phase':        'pick',
        'p1_ready':     False,
        'p2_ready':     False,
        'p1_picked':    None,
        'p2_picked':    None,
        'candidates':   group_candidates,
        'banned_ids':   list(all_bans),
    }
    group_img = await card_renderer.generate_bsk_pool_group_card_async(group_card_data)
    group_caption = (
        f"🗳 <b>Round {round_num} — Pick Phase{test_tag}</b>\n"
        f"Maps sent privately. ⏳ {PICK_TIMEOUT_SECONDS}s to pick"
    )
    try:
        new_mid = await _send_or_edit_photo(
            bot, chat_id, message_id, group_img, caption=group_caption,
        )
        if new_mid != message_id:
            async with get_db_session() as _s:
                _d = (await _s.execute(
                    select(BskDuel).where(BskDuel.id == duel_id)
                )).scalar_one_or_none()
                if _d:
                    _d.message_id = new_mid
                    await _s.commit()
    except Exception as e:
        logger.error(f"_resolve_ban: group card update failed: {e}")

    # Send DM pick cards
    dm_pick_kb  = _pick_keyboard(duel_id, new_maps)
    dm_caption  = (
        f"🗳 <b>Round {round_num} · Your map pool{test_tag}</b>\n"
        f"Pick a map to play. ⏳ {PICK_TIMEOUT_SECONDS}s"
    )

    async def _send_dm_pick(tg_id: int, player_name: str, player_country: str,
                             priority: bool) -> Optional[int]:
        dm_data = {
            'round_number':   round_num,
            'player_name':    player_name,
            'player_country': player_country,
            'phase':          'pick',
            'priority':       priority,
            'banned_ids':     list(all_bans),
            'ban_count':      0,
            'max_bans':       0,
            'candidates':     dm_candidates,
        }
        img = await card_renderer.generate_bsk_pool_dm_card_async(dm_data)
        img.seek(0)
        try:
            msg = await bot.send_photo(
                tg_id,
                photo=BufferedInputFile(img.read(), filename='pool.png'),
                caption=dm_caption,
                parse_mode='HTML',
                reply_markup=dm_pick_kb,
            )
            return msg.message_id
        except Exception as exc:
            logger.warning(f"_resolve_ban: DM pick to tg_id={tg_id} failed — {exc}")
            return None

    p1_dm_msg = None
    p2_dm_msg = None
    if p1_tg_id:
        p1_dm_msg = await _send_dm_pick(p1_tg_id, p1_name, p1_country, p1_priority)
    if p2_tg_id and p2_tg_id != p1_tg_id:
        p2_dm_msg = await _send_dm_pick(p2_tg_id, p2_name, p2_country, not p1_priority)

    # Store pick DM state
    _pick_dm_state[duel_id] = {
        'p1_tg_id':    p1_tg_id,
        'p2_tg_id':    p2_tg_id,
        'p1_dm_msg':   p1_dm_msg,
        'p2_dm_msg':   p2_dm_msg,
        'candidates':  group_candidates,
        'round_num':   round_num,
        'p1_name':     p1_name,
        'p2_name':     p2_name,
        'p1_country':  p1_country,
        'p2_country':  p2_country,
        'p1_priority': p1_priority,
    }

    asyncio.create_task(
        _expire_pick(bot, duel_id, osu_api, PICK_TIMEOUT_SECONDS, expected_candidates)
    )


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


async def submit_pick(bot: Bot, duel_id: int, user_id: int, beatmap_id: int) -> str:
    """
    Register a player's map pick. Returns:
      'ok'      — pick registered, waiting for the other player
      'done'    — both picked, round starting
      'invalid' — duel not in pick phase or user not a participant
      'already' — player already picked
    """
    import random as _random

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()

        if not duel or not duel.pick_candidates:
            return 'invalid'
        if duel.status not in ('accepted', 'round_active'):
            return 'invalid'

        candidate_ids = [int(x) for x in duel.pick_candidates.split(",") if x]
        if beatmap_id not in candidate_ids:
            return 'invalid'

        # Capture player IDs before session closes
        duel_player1_id = duel.player1_user_id
        duel_player2_id = duel.player2_user_id

        # Test duel: both slots belong to the same user — register for both at once
        if duel.is_test and duel_player1_id == duel_player2_id:
            if user_id != duel_player1_id:
                return 'invalid'
            if duel.pick_p1 is not None:
                return 'already'
            duel.pick_p1 = beatmap_id
            duel.pick_p2 = beatmap_id
        elif user_id == duel_player1_id:
            if duel.pick_p1 is not None:
                return 'already'
            duel.pick_p1 = beatmap_id
        elif user_id == duel_player2_id:
            if duel.pick_p2 is not None:
                return 'already'
            duel.pick_p2 = beatmap_id
        else:
            return 'invalid'

        await session.commit()

        both_done = duel.pick_p1 is not None and duel.pick_p2 is not None

    if both_done:
        await _resolve_pick(bot, duel_id, osu_api=_osu_api)
        return 'done'

    # ── One player picked — update group face-down card + edit picker's DM ───
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            return 'ok'
        pick_p1 = duel.pick_p1
        pick_p2 = duel.pick_p2
        chat_id    = duel.chat_id
        message_id = duel.message_id
        round_num  = duel.current_round + 1

    dm_state = _pick_dm_state.get(duel_id, {})
    group_candidates = dm_state.get('candidates', [])

    from services.image import card_renderer

    # Re-render group card with pick highlight
    group_card_data = {
        'round_number': round_num,
        'p1_name':    dm_state.get('p1_name', 'P1'),
        'p2_name':    dm_state.get('p2_name', 'P2'),
        'p1_country': dm_state.get('p1_country', ''),
        'p2_country': dm_state.get('p2_country', ''),
        'phase':      'pick',
        'p1_ready':   pick_p1 is not None,
        'p2_ready':   pick_p2 is not None,
        'p1_picked':  pick_p1,
        'p2_picked':  pick_p2,
        'candidates': group_candidates,
        'banned_ids': [],
    }
    try:
        group_img = await card_renderer.generate_bsk_pool_group_card_async(group_card_data)
        caption = f"🗳 <b>Раунд {round_num} — выбор карты</b>\n⏳ Ожидаем второго игрока…"
        await _send_or_edit_photo(bot, chat_id, message_id, group_img, caption=caption)
    except Exception as e:
        logger.warning(f"submit_pick: failed to update group card: {e}")

    # Remove pick keyboard from the player who just picked (they're done)
    picker_tg_id = None
    picker_dm_msg = None
    if user_id == duel_player1_id:
        picker_tg_id  = dm_state.get('p1_tg_id')
        picker_dm_msg = dm_state.get('p1_dm_msg')
    elif user_id == duel_player2_id:
        picker_tg_id  = dm_state.get('p2_tg_id')
        picker_dm_msg = dm_state.get('p2_dm_msg')

    if picker_tg_id and picker_dm_msg:
        try:
            await bot.edit_message_caption(
                chat_id=picker_tg_id,
                message_id=picker_dm_msg,
                caption="✅ <b>Выбор принят!</b> Ждём второго игрока…",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception as e:
            logger.debug(f"submit_pick: could not edit picker DM caption: {e}")

    return 'ok'


async def _resolve_pick(bot: Bot, duel_id: int, osu_api) -> None:
    """Resolve pick phase: determine which map to play and start the round."""
    import random as _random

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or not duel.pick_candidates:
            return

        candidate_ids = [int(x) for x in duel.pick_candidates.split(",") if x]

        p1_pick = duel.pick_p1
        p2_pick = duel.pick_p2

        # Determine winning map_id
        if p1_pick is not None and p2_pick is not None:
            chosen_id = p1_pick if p1_pick == p2_pick else _random.choice([p1_pick, p2_pick])
        elif p1_pick is not None:
            chosen_id = p1_pick
        elif p2_pick is not None:
            chosen_id = p2_pick
        else:
            chosen_id = _random.choice(candidate_ids) if candidate_ids else None

        # Clear pick state
        duel.pick_candidates = None
        duel.pick_p1 = None
        duel.pick_p2 = None
        await session.commit()

        beatmap = None
        if chosen_id:
            beatmap = (await session.execute(
                select(BskMapPool).where(BskMapPool.beatmap_id == chosen_id)
            )).scalar_one_or_none()

    # Clean up in-memory DM state (outside session)
    _pick_dm_state.pop(duel_id, None)

    await _start_next_round(bot, duel_id, osu_api, forced_map=beatmap)


async def _expire_pick(bot: Bot, duel_id: int, osu_api, delay: int, expected_candidates: str) -> None:
    """After `delay` seconds, auto-resolve the pick phase if still pending.

    `expected_candidates` is the pick_candidates string at the moment this timer
    was created. We compare it against the current DB value to ensure we are
    expiring the *same* pick phase and not a newer one (which would happen if a
    fast round caused the timer from round N to fire during round N+1 pick phase).
    """
    await asyncio.sleep(delay)

    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or not duel.pick_candidates:
            return  # already resolved
        if duel.pick_candidates != expected_candidates:
            return  # stale timer — belongs to a previous pick phase

    logger.info(f"_expire_pick: auto-resolving pick for duel {duel_id}")
    await _resolve_pick(bot, duel_id, osu_api)


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
            duel.current_star_rating = _base_sr_for_duel(r1, r2)
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
        pause_kb = InlineKeyboardMarkup(inline_keyboard=[control_row])

        # Build round start card
        from services.image import card_renderer
        ml_winner_val = None
        ml_conf_val = None
        if r1 and r2:
            _ml_w, _ml_c = predict_round_winner(
                p1_mu_aim=r1.mu_aim, p1_mu_speed=r1.mu_speed,
                p1_mu_acc=r1.mu_acc, p1_mu_cons=r1.mu_cons,
                p2_mu_aim=r2.mu_aim, p2_mu_speed=r2.mu_speed,
                p2_mu_acc=r2.mu_acc, p2_mu_cons=r2.mu_cons,
                w_aim=beatmap.w_aim or 0.25, w_speed=beatmap.w_speed or 0.25,
                w_acc=beatmap.w_acc or 0.25, w_cons=beatmap.w_cons or 0.25,
            )
            ml_winner_val = _ml_w
            ml_conf_val = _ml_c

        round_card_data = {
            'round_number': duel.current_round,
            'p1_name': p1_name,
            'p2_name': p2_name,
            'p1_country': p1_country,
            'p2_country': p2_country,
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
        _current_round = duel.current_round
        _is_test       = duel.is_test
        _beatmap_id    = beatmap.beatmap_id

    # ── Outside session: card rendering + Telegram IO ──────────────────────
    # Any exception here must NOT prevent the monitor task from starting.
    try:
        img_bytes = await card_renderer.generate_bsk_round_start_card_async(round_card_data)
        test_tag = ' [ТЕСТ]' if _is_test else ''
        caption = (
            f"🎮 <b>Раунд {_current_round}{test_tag}</b>\n"
            f"🔗 https://osu.ppy.sh/b/{_beatmap_id}\n"
            f"⏱ Форфейт через <b>{forfeit_mins} мин</b>"
        )
        new_mid = await _send_or_edit_photo(
            bot, _chat_id, _message_id,
            img_bytes, caption=caption, reply_markup=pause_kb,
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
        asyncio.create_task(_safe_monitor_round(bot, duel_id, _round_entry_id, osu_api))


MAX_MONITOR_HOURS = 2


async def _monitor_round(bot: Bot, duel_id: int, round_id: int, osu_api) -> None:
    """Poll recent scores for both players until both submit or forfeit."""
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
                stats = score.get('statistics') or {}
                # stable: statistics.miss, lazer: statistics.miss or statistics.legacy_combo_increase
                misses = int(
                    stats.get('miss') or
                    stats.get('count_miss') or
                    0
                )
                max_combo = int((score.get('beatmap') or {}).get('max_combo') or combo or 1)
                comp = composite_score(pp, acc, combo, max_combo, misses)
                pts = composite_points(pp, acc, combo, max_combo, misses)

                setattr(rnd, f'player{player_num}_score', int(score.get('score') or 0))
                setattr(rnd, f'player{player_num}_accuracy', acc)
                setattr(rnd, f'player{player_num}_combo', combo)
                setattr(rnd, f'player{player_num}_misses', misses)
                setattr(rnd, f'player{player_num}_pp', pp)
                setattr(rnd, f'player{player_num}_composite', comp)
                setattr(rnd, f'player{player_num}_points', pts)
                setattr(rnd, f'player{player_num}_submitted_at', now)

            # Both submitted?
            if rnd.player1_composite is not None and rnd.player2_composite is not None:
                await _complete_round(bot, duel, rnd, session)
                return

            await session.commit()


async def _find_score_on_map(osu_api, token: str, osu_user_id: int, beatmap_id: int, after: datetime):
    """Check recent scores for a score on beatmap_id submitted after `after`."""
    import aiohttp
    url = f"https://osu.ppy.sh/api/v2/users/{osu_user_id}/scores/recent"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 10, "include_fails": 0, "mode": "osu"}

    max_retries = 3
    scores = None
    last_error = None
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers=headers, params=params) as resp:
                    if resp.status == 429:
                        retry_after = min(float(resp.headers.get("Retry-After", "5")), 30)
                        logger.warning(f"_find_score: 429 for user {osu_user_id}, retry in {retry_after}s ({attempt+1}/{max_retries})")
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status >= 500:
                        logger.warning(f"_find_score: HTTP {resp.status} for user {osu_user_id} ({attempt+1}/{max_retries})")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if resp.status != 200:
                        logger.warning(f"_find_score: HTTP {resp.status} for user {osu_user_id}")
                        return None
                    scores = await resp.json()
                    break
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = e
            logger.warning(f"_find_score: network error for user {osu_user_id}: {e} ({attempt+1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            continue

    if scores is None:
        if last_error:
            logger.error(f"_find_score: all {max_retries} retries failed for user {osu_user_id}: {last_error}")
        return None

    for sc in scores:
        if int((sc.get('beatmap') or {}).get('id') or 0) != beatmap_id:
            continue
        logger.debug(f"score statistics for user {osu_user_id}: {sc.get('statistics')}")
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
        except (ValueError, TypeError) as e:
            logger.warning(f"_find_score: bad timestamp '{created_at}' for user {osu_user_id}: {e}")
            continue
    return None


async def _complete_round(bot: Bot, duel: BskDuel, rnd: BskDuelRound, session) -> None:
    c1 = rnd.player1_composite or 0
    c2 = rnd.player2_composite or 0
    pts1 = rnd.player1_points if rnd.player1_points is not None else composite_points(
        rnd.player1_pp or 0, rnd.player1_accuracy or 0,
        rnd.player1_combo or 0, max(rnd.player1_combo or 1, 1), rnd.player1_misses or 0,
    )
    pts2 = rnd.player2_points if rnd.player2_points is not None else composite_points(
        rnd.player2_pp or 0, rnd.player2_accuracy or 0,
        rnd.player2_combo or 0, max(rnd.player2_combo or 1, 1), rnd.player2_misses or 0,
    )
    rnd.player1_points = pts1
    rnd.player2_points = pts2

    winner = 1 if c1 >= c2 else 2

    rnd.winner_player = winner
    rnd.status = 'completed'
    rnd.completed_at = datetime.now(timezone.utc)

    duel.player1_total_score += pts1
    duel.player2_total_score += pts2

    # Save per-round rating snapshots
    from db.models.bsk_rating import BskRating
    from sqlalchemy import select as sa_select
    r1 = (await session.execute(
        sa_select(BskRating).where(BskRating.user_id == duel.player1_user_id, BskRating.mode == duel.mode)
    )).scalar_one_or_none()
    r2 = (await session.execute(
        sa_select(BskRating).where(BskRating.user_id == duel.player2_user_id, BskRating.mode == duel.mode)
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
    p1_name = p1.osu_username if p1 else "Игрок 1"
    p2_name = p2.osu_username if p2 else "Игрок 2"
    winner_name = p1_name if winner == 1 else p2_name

    # Update ratings per-round (non-test only), then save after snapshots
    map_weights = {
        'aim':   rnd.w_aim   or 0.25,
        'speed': rnd.w_speed or 0.25,
        'acc':   rnd.w_acc   or 0.25,
        'cons':  rnd.w_cons  or 0.25,
    }
    if not duel.is_test:
        winner_uid = duel.player1_user_id if winner == 1 else duel.player2_user_id
        loser_uid  = duel.player2_user_id if winner == 1 else duel.player1_user_id
        await session.commit()
        w_rating, l_rating = await update_ratings(winner_uid, loser_uid, duel.mode, map_weights=map_weights)
        # Save after-snapshots in the same session (still open from caller)
        rnd_fresh = (await session.execute(
            sa_select(BskDuelRound).where(BskDuelRound.id == rnd.id)
        )).scalar_one_or_none()
        if rnd_fresh:
            if winner == 1:
                rnd_fresh.p1_mu_aim_after   = w_rating.mu_aim
                rnd_fresh.p1_mu_speed_after = w_rating.mu_speed
                rnd_fresh.p1_mu_acc_after   = w_rating.mu_acc
                rnd_fresh.p1_mu_cons_after  = w_rating.mu_cons
                rnd_fresh.p2_mu_aim_after   = l_rating.mu_aim
                rnd_fresh.p2_mu_speed_after = l_rating.mu_speed
                rnd_fresh.p2_mu_acc_after   = l_rating.mu_acc
                rnd_fresh.p2_mu_cons_after  = l_rating.mu_cons
            else:
                rnd_fresh.p2_mu_aim_after   = w_rating.mu_aim
                rnd_fresh.p2_mu_speed_after = w_rating.mu_speed
                rnd_fresh.p2_mu_acc_after   = w_rating.mu_acc
                rnd_fresh.p2_mu_cons_after  = w_rating.mu_cons
                rnd_fresh.p1_mu_aim_after   = l_rating.mu_aim
                rnd_fresh.p1_mu_speed_after = l_rating.mu_speed
                rnd_fresh.p1_mu_acc_after   = l_rating.mu_acc
                rnd_fresh.p1_mu_cons_after  = l_rating.mu_cons
            await session.commit()
    else:
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
        caption = f"✅ <b>Раунд {rnd.round_number} завершён!</b>  {next_line}"
        await _send_or_edit_photo(
            bot, duel.chat_id, duel.message_id,
            img_bytes, caption=caption,
        )
    except Exception as e:
        logger.error(f"_complete_round: failed to send result card: {e}")

    asyncio.create_task(_next_round_delayed(bot, duel.id, 15))


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

    duel.player1_total_score += rnd.player1_points or 0
    duel.player2_total_score += rnd.player2_points or 0

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
        await session.commit()  # flush before update_ratings opens its own session
        try:
            await update_ratings(winner_uid, loser_uid, duel.mode, map_weights=map_weights)
        except Exception as e:
            logger.error(f"_handle_forfeit: update_ratings failed: {e}", exc_info=True)

    if rnd.winner_player:
        winner_name = p1_name if rnd.winner_player == 1 else p2_name
        loser_name = p2_name if rnd.winner_player == 1 else p1_name
        msg = (
            f"⏰ <b>Время вышло!</b>\n\n"
            f"<b>{loser_name}</b> не успел сыграть карту.\n"
            f"Раунд {rnd.round_number} засчитан <b>{winner_name}</b> по forfeit.\n\n"
            f"📊 Счёт: <b>{int(duel.player1_total_score):,}</b> — <b>{int(duel.player2_total_score):,}</b>"
        )
    else:
        msg = (
            f"⏰ <b>Время вышло!</b>\n\n"
            f"Оба игрока не сыграли карту — раунд аннулирован.\n\n"
            f"📊 Счёт: <b>{int(duel.player1_total_score):,}</b> — <b>{int(duel.player2_total_score):,}</b>"
        )

    try:
        await bot.edit_message_text(msg, chat_id=duel.chat_id, message_id=duel.message_id, parse_mode="HTML")
    except Exception:
        pass

    asyncio.create_task(_next_round_delayed(bot, duel.id, 15))


async def _next_round_delayed(bot: Bot, duel_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    try:
        await _start_pick_phase(bot, duel_id, _osu_api)
    except Exception as e:
        logger.error(f"_next_round_delayed error for duel {duel_id}: {e}", exc_info=True)


async def _finish_duel(bot: Bot, duel_id: int) -> None:
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel:
            return

        p1 = await _get_user(session, duel.player1_user_id)
        p2 = await _get_user(session, duel.player2_user_id)

        # Extract all values before session closes
        p1_name = p1.osu_username if p1 else "Игрок 1"
        p2_name = p2.osu_username if p2 else "Игрок 2"
        p1_country = (p1.country or '') if p1 else ''
        p2_country = (p2.country or '') if p2 else ''
        s1 = duel.player1_total_score
        s2_score = duel.player2_total_score
        is_test = duel.is_test
        mode = duel.mode
        current_round = duel.current_round
        chat_id = duel.chat_id
        message_id = duel.message_id
        p1_uid = duel.player1_user_id

        if s1 > s2_score:
            winner_id = duel.player1_user_id
        elif s2_score > s1:
            winner_id = duel.player2_user_id
        else:
            winner_id = None

        duel.status = 'completed'
        duel.completed_at = datetime.now(timezone.utc)
        duel.winner_user_id = winner_id

        # Update per-user duel win/loss counters (non-test only)
        if not is_test and winner_id is not None:
            if p1:
                if winner_id == duel.player1_user_id:
                    p1.duel_wins = (p1.duel_wins or 0) + 1
                else:
                    p1.duel_losses = (p1.duel_losses or 0) + 1
            if p2:
                if winner_id == duel.player2_user_id:
                    p2.duel_wins = (p2.duel_wins or 0) + 1
                else:
                    p2.duel_losses = (p2.duel_losses or 0) + 1

        await session.commit()

    try:
        from services.image import card_renderer
        from db.models.bsk_duel_round import BskDuelRound as _BskDuelRound
        from db.models.bsk_rating import BskRating as _BskRating

        async with get_db_session() as _fsess:
            rounds_db = (await _fsess.execute(
                select(_BskDuelRound)
                .where(_BskDuelRound.duel_id == duel_id)
                .order_by(_BskDuelRound.round_number)
            )).scalars().all()

            # Compute per-skill deltas from before/after snapshots
            p1_deltas = {}
            p2_deltas = {}
            for comp in ('aim', 'speed', 'acc', 'cons'):
                p1_before = next((getattr(r, f'p1_mu_{comp}_before') for r in rounds_db if getattr(r, f'p1_mu_{comp}_before') is not None), None)
                p1_after  = next((getattr(r, f'p1_mu_{comp}_after')  for r in reversed(rounds_db) if getattr(r, f'p1_mu_{comp}_after') is not None), None)
                p2_before = next((getattr(r, f'p2_mu_{comp}_before') for r in rounds_db if getattr(r, f'p2_mu_{comp}_before') is not None), None)
                p2_after  = next((getattr(r, f'p2_mu_{comp}_after')  for r in reversed(rounds_db) if getattr(r, f'p2_mu_{comp}_after') is not None), None)
                p1_deltas[comp] = (p1_after - p1_before) if (p1_before is not None and p1_after is not None) else None
                p2_deltas[comp] = (p2_after - p2_before) if (p2_before is not None and p2_after is not None) else None

        round_history = [
            {
                'round_number': r.round_number,
                'beatmap_title': r.beatmap_title or 'Unknown',
                'star_rating': float(r.star_rating or 0),
                'winner': r.winner_player or 0,
                'p1_points': int(r.player1_points or 0),
                'p2_points': int(r.player2_points or 0),
            }
            for r in rounds_db
        ]

        winner_num = 1 if (winner_id and winner_id == p1_uid) else 2 if winner_id else 0
        end_card_data = {
            'p1_name': p1_name,
            'p2_name': p2_name,
            'p1_country': p1_country,
            'p2_country': p2_country,
            'winner': winner_num,
            'score_p1': int(s1),
            'score_p2': int(s2_score),
            'mode': mode,
            'total_rounds': current_round,
            'is_test': is_test,
            'rounds': round_history,
        }
        for comp in ('aim', 'speed', 'acc', 'cons'):
            end_card_data[f'p1_delta_{comp}'] = p1_deltas.get(comp)
            end_card_data[f'p2_delta_{comp}'] = p2_deltas.get(comp)

        img_bytes = await card_renderer.generate_bsk_duel_end_card_async(end_card_data)
        caption = "🎉 <b>ДУЭЛЬ ЗАВЕРШЕНА!</b>" + (" <i>[ТЕСТ]</i>" if is_test else "")
        await _send_or_edit_photo(
            bot, chat_id, message_id,
            img_bytes, caption=caption,
        )
    except Exception as e:
        logger.error(f"_finish_duel: failed to send end card: {e}", exc_info=True)


async def _expire_duel(bot: Bot, duel_id: int, osu_api) -> None:
    await asyncio.sleep(ACCEPT_TIMEOUT_MINUTES * 60)
    async with get_db_session() as session:
        duel = (await session.execute(
            select(BskDuel).where(BskDuel.id == duel_id)
        )).scalar_one_or_none()
        if not duel or duel.status != 'pending':
            return
        duel.status = 'expired'
        p2 = await _get_user(session, duel.player2_user_id)
        await session.commit()

        try:
            await bot.edit_message_text(
                f"⏰ <b>Вызов истёк</b>\n\n"
                f"<i>{p2.osu_username if p2 else 'Соперник'} не ответил в течение {ACCEPT_TIMEOUT_MINUTES} минут.</i>\n"
                "Дуэль отменена.",
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
            total_rounds=0,
            target_score=TARGET_SCORE,
            accepted_at=datetime.now(timezone.utc),
            version=2,
        )
        session.add(duel)
        await session.commit()
        await session.refresh(duel)

        msg = await bot.send_message(
            chat_id,
            f"🧪 <b>ТЕСТОВАЯ ДУЭЛЬ</b>\n\n"
            f"Игрок: <b>{user.osu_username}</b> (оба слота)\n"
            f"Режим: <b>{mode.upper()}</b> · цель {TARGET_SCORE:,} pts\n\n"
            f"<i>Выбирай карту для каждого раунда.\n"
            f"bsktestround — симулировать раунд\n"
            f"bsktestend   — завершить</i>",
            parse_mode="HTML",
        )
        duel.message_id = msg.message_id
        await session.commit()

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
        rnd.player1_points = composite_points(p1_pp, p1_acc, p1_combo, max_combo, p1_misses)
        rnd.player1_submitted_at = datetime.now(timezone.utc)

        rnd.player2_pp = p2_pp
        rnd.player2_accuracy = p2_acc
        rnd.player2_combo = p2_combo
        rnd.player2_misses = p2_misses
        rnd.player2_composite = composite_score(p2_pp, p2_acc, p2_combo, max_combo, p2_misses)
        rnd.player2_points = composite_points(p2_pp, p2_acc, p2_combo, max_combo, p2_misses)
        rnd.player2_submitted_at = datetime.now(timezone.utc)

        await _complete_round(bot, duel, rnd, session)
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
                )
            except Exception:
                pass
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
                    f"⏸ <b>Дуэль приостановлена</b>\n\n"
                    f"Оба игрока проголосовали за паузу.\n"
                    f"Время форфейта продлено на <b>15 минут</b>.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
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
        await session.commit()

    try:
        await bot.send_message(
            chat_id,
            "▶️ <b>Дуэль возобновлена!</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
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
        duel.pick_p1 = None
        duel.pick_p2 = None
        await session.commit()

    _pick_dm_state.pop(duel_id, None)
    _ban_state.pop(duel_id, None)

    cancel_text = (
        "❌ Вызов отменён инициатором."
        if duel.player1_user_id == user_id
        else "❌ <b>Дуэль отменена соперником.</b>"
    )
    try:
        await bot.edit_message_text(
            cancel_text,
            chat_id=duel.chat_id,
            message_id=duel.message_id,
            parse_mode="HTML",
        )
    except Exception:
        pass
    return 'cancelled'


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
        duel.pick_p1 = None
        duel.pick_p2 = None
        await session.commit()

    _pick_dm_state.pop(duel_id, None)
    _ban_state.pop(duel_id, None)

    try:
        await bot.edit_message_text(
            "❌ <b>Тестовая дуэль отменена.</b>",
            chat_id=duel.chat_id,
            message_id=duel.message_id,
            parse_mode="HTML",
        )
    except Exception:
        pass
    return True
