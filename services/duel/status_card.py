"""Live duel status card.

Posts a head-to-head scoreboard for an active duel and edits it **in place**
as the match progresses (no per-round spam), and provides the shared data
assembly used by both the engine and the ``/duelstatus`` command.

The posted message id is tracked in-memory (best effort): it is not persisted,
so after a restart the recovery pass simply posts a fresh card and the old one
goes stale — acceptable for a convenience scoreboard.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InputMediaPhoto
from sqlalchemy import select

from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_rating import DuelRating
from db.models.duel_round import DuelRound
from db.models.user import User
from services.image import card_renderer
from utils.hp_calculator import get_division_for_conservative
from utils.logger import get_logger

logger = get_logger("duel.status_card")

# duel_id -> (chat_id, message_id) of the live status card.
_live: dict[int, tuple[int, int]] = {}

# duel_id -> current caption (round/finish result) shown under the live card.
_caption: dict[int, str] = {}

# duel_id -> {"player": 1|2, "name": str} while a player is picking their map.
_pick_state: dict[int, dict] = {}


def set_pick_state(duel_id: int, player: int, name: str) -> None:
    """Mark the duel as awaiting a map pick from ``player`` (1/2)."""
    _pick_state[duel_id] = {"player": player, "name": name}


def clear_pick_state(duel_id: int) -> None:
    _pick_state.pop(duel_id, None)


def _player_dict(u, rating, mode: str) -> dict:
    if not u:
        return {"username": "???", "country": "", "avatar_url": None,
                "division": "", "mu": 0.0, "calibrating": False, "placement_left": 0}
    division = ""
    mu = 0.0
    placement_left = 0
    if rating:
        mu = rating.mu
        placement_left = rating.placement_matches_left or 0
        if mode == "ranked":
            division = get_division_for_conservative(rating.conservative)
    return {
        "username": u.osu_username or "???",
        "country": u.country or "",
        "avatar_url": u.avatar_url,
        "cover_data": bytes(u.cover_data) if u.cover_data else None,
        "cover_url": u.cover_url,
        "division": division,
        "mu": mu,
        # During placement the conservative-based division is uncertainty-
        # deflated and misleading → the card shows a CALIBRATING badge instead.
        "calibrating": placement_left > 0,
        "placement_left": placement_left,
    }


async def assemble_status_data(session, duel: Duel) -> dict:
    """Build the status-card data dict from a loaded duel (within its session)."""
    p1 = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one_or_none()
    p2 = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one_or_none()

    ratings = {
        r.user_id: r for r in (await session.execute(
            select(DuelRating).where(
                DuelRating.mode == duel.mode,
                DuelRating.user_id.in_([duel.player1_user_id, duel.player2_user_id]),
            )
        )).scalars().all()
    }

    rounds = (await session.execute(
        select(DuelRound)
        .where(DuelRound.duel_id == duel.id)
        .order_by(DuelRound.round_number.asc())
    )).scalars().all()

    winner_player = None
    if duel.status == "completed" and duel.winner_user_id:
        winner_player = 1 if duel.winner_user_id == duel.player1_user_id else 2

    cur_map = None
    if duel.status == "round_active":
        playing = next((r for r in rounds if r.status == "playing"), None)
        if playing:
            cur_map = {
                "title": playing.beatmap_title or "???",
                "star_rating": playing.star_rating or 0.0,
                "beatmap_id": playing.beatmap_id,
                "beatmapset_id": playing.beatmapset_id,
            }

    return {
        "mode": duel.mode,
        "status": duel.status,
        "total_rounds": duel.total_rounds,
        "win_target": duel.win_target,
        "current_round": duel.current_round,
        "score": (duel.player1_rounds_won, duel.player2_rounds_won),
        "p1": _player_dict(p1, ratings.get(duel.player1_user_id), duel.mode),
        "p2": _player_dict(p2, ratings.get(duel.player2_user_id), duel.mode),
        "rounds": [{"status": r.status, "winner": r.winner_player} for r in rounds],
        "current_map": cur_map,
        "picking": _pick_state.get(duel.id),
        "winner_player": winner_player,
        "chat_id": duel.chat_id,
        "thread_id": duel.message_thread_id,
    }


async def load_status_data(duel_id: int) -> dict | None:
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel:
            return None
        return await assemble_status_data(session, duel)


async def post_or_update(bot: Bot, duel_id: int, caption: str | None = None) -> None:
    """Render the live card and edit it in place, or post fresh the first time.

    ``caption`` (HTML) becomes the card's caption and is remembered for later
    re-renders until changed — so round and finish results live as the caption
    *under* the live card instead of separate messages spamming the topic.

    Best-effort — never raises, so a duel task is never killed by a Telegram or
    render hiccup.
    """
    if caption is not None:
        _caption[duel_id] = caption
    cap = _caption.get(duel_id)

    data = await load_status_data(duel_id)
    if not data or not data.get("chat_id"):
        return
    try:
        buf = await card_renderer.generate_duel_status_card_async(data)
        png = buf.getvalue()
    except Exception as e:
        logger.error(f"duel {duel_id}: status card render failed: {e}", exc_info=True)
        return

    chat_id = data["chat_id"]
    thread_id = data.get("thread_id")

    existing = _live.get(duel_id)
    if existing:
        ch, mid = existing
        try:
            await bot.edit_message_media(
                chat_id=ch, message_id=mid,
                media=InputMediaPhoto(
                    media=BufferedInputFile(png, filename="duel_status.png"),
                    caption=cap, parse_mode="HTML",
                ),
            )
            return
        except TelegramBadRequest as e:
            txt = str(e).lower()
            if "not modified" in txt:
                return  # card already current — never repost (avoids duplicates)
            if ("message to edit not found" in txt or "can't be edited" in txt
                    or "message_id_invalid" in txt):
                _live.pop(duel_id, None)  # message is gone → fall through to repost
            else:
                # Transient (rate limit, network, etc.) — keep the existing card
                # and retry on the next update instead of spawning a duplicate.
                logger.debug(f"duel {duel_id}: status edit failed, keeping card: {e}")
                return
        except Exception:
            logger.debug(f"duel {duel_id}: status edit error, keeping card", exc_info=True)
            return  # don't duplicate the card on unknown errors

    try:
        msg = await bot.send_photo(
            chat_id,
            BufferedInputFile(png, filename="duel_status.png"),
            caption=cap, parse_mode="HTML",
            message_thread_id=thread_id,
        )
        _live[duel_id] = (chat_id, msg.message_id)
    except Exception:
        logger.debug(f"duel {duel_id}: status card post failed", exc_info=True)


def clear(duel_id: int) -> None:
    """Forget the live card for a finished/cancelled duel (frees memory; the
    message itself is left in the chat)."""
    _live.pop(duel_id, None)
    _pick_state.pop(duel_id, None)
    _caption.pop(duel_id, None)
