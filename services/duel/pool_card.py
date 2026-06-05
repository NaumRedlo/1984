"""Per-player duel pool card — one live photo message that is each player's
control surface for the whole duel.

On accept every player is DM'd their 6-map pool card.  This module owns that
message: it re-renders + edits it in place as the duel moves through its phases
(pool swap → per-round pick → played), so the swap/pick keyboards ride on the
card itself and already-played maps get a diagonal "PLAYED" stamp.  The
interaction state machines still live in :mod:`pool_swap` / :mod:`pick_phase`;
they just delegate all drawing here.

State is in-memory, keyed by ``(duel_id, tg_id)`` (mirrors the rest of the duel
state).  On a restart it's forgotten — :func:`show` then simply sends a fresh
card and adopts it, with ``order`` / ``played`` re-seeded by the engine from the
DB, so the card heals itself without a migration.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto
from sqlalchemy import select

from db.database import get_db_session
from db.models.duel_map_pool import DuelMapPool
from services.duel.duel_constants import pool_size_for, win_target_for
from services.image import card_renderer
from services.image.utils import download_image
from utils.logger import get_logger

logger = get_logger("duel.pool_card")

# (duel_id, tg_id) -> state dict (see _new_state).
_cards: dict[tuple[int, int], dict] = {}

_COVER_URL = "https://assets.ppy.sh/beatmaps/{}/covers/cover.jpg"


def _state(duel_id: int, tg_id: int) -> Optional[dict]:
    return _cards.get((duel_id, tg_id))


def ensure(duel_id: int, tg_id: int, *, chat_id: int, order: list[int],
           mode: str, target_sr: float) -> None:
    """Register a player's pool card if not already tracked (create-if-absent).

    Called on accept (before the first send) and again by the engine on resume —
    on resume there is no message yet, so the next :func:`show` sends a fresh
    card and adopts its id."""
    key = (duel_id, tg_id)
    if key in _cards:
        return
    _cards[key] = {
        "chat_id": chat_id,
        "message_id": None,
        "order": list(order),
        "played": set(),
        "mode": mode,
        "target_sr": float(target_sr or 0.0),
        "render_cache": {},   # beatmap_id -> render dict
        "cover_cache": {},    # beatmapset_id -> PIL Image | None
        "last_key": None,     # (tuple(order), frozenset(played)) of last drawn image
    }


def set_order(duel_id: int, tg_id: int, order: list[int]) -> None:
    st = _state(duel_id, tg_id)
    if st is not None:
        st["order"] = list(order)


def mark_played(duel_id: int, tg_id: int, beatmap_id: int) -> None:
    st = _state(duel_id, tg_id)
    if st is not None:
        st["played"].add(int(beatmap_id))


def clear(duel_id: int) -> None:
    for key in [k for k in _cards if k[0] == duel_id]:
        _cards.pop(key, None)


# ── rendering ────────────────────────────────────────────────────────────────
async def _render_dicts(st: dict) -> list[dict]:
    """Full render dicts for ``st['order']`` (cached per beatmap_id), each tagged
    with ``status`` = played | available."""
    order = st["order"]
    cache = st["render_cache"]
    missing = [b for b in order if b not in cache]
    if missing:
        async with get_db_session() as session:
            rows = (await session.execute(
                select(DuelMapPool).where(DuelMapPool.beatmap_id.in_(missing))
            )).scalars().all()
        by_id = {r.beatmap_id: r for r in rows}
        for b in missing:
            r = by_id.get(b)
            cache[b] = {
                "beatmap_id": b,
                "artist": (r.artist if r else ""),
                "title": (r.title if r else f"map {b}"),
                "version": (r.version if r else ""),
                "creator": (r.creator if r else ""),
                "star_rating": (r.star_rating if r else 0.0),
                "length": (r.length if r else 0),
                "bpm": (r.bpm if r else 0),
                "max_combo": (r.max_combo if r else 0),
                "beatmapset_id": (r.beatmapset_id if r else 0),
                "cs": (r.cs if r else 0), "ar": (r.ar if r else 0),
                "od": (r.od if r else 0), "hp_drain": (r.hp_drain if r else 0),
            }
    played = st["played"]
    out = []
    for b in order:
        d = dict(cache[b])
        d["status"] = "played" if b in played else "available"
        out.append(d)
    return out


async def _covers_for(st: dict, maps: list[dict]) -> list:
    """Beatmap covers for ``maps``, cached per beatmapset_id so an edit only
    fetches a newly-swapped map's cover."""
    cache = st["cover_cache"]

    async def _one(bsid):
        bsid = int(bsid or 0)
        if bsid <= 0:
            return None
        if bsid in cache:
            return cache[bsid]
        img = await download_image(_COVER_URL.format(bsid))
        img = img if (img and not isinstance(img, Exception)) else None
        cache[bsid] = img
        return img

    return list(await asyncio.gather(*[_one(m.get("beatmapset_id")) for m in maps]))


async def _render_png(st: dict) -> bytes:
    maps = await _render_dicts(st)
    covers = await _covers_for(st, maps)
    data = {
        "mode": st["mode"],
        "total_rounds": pool_size_for(st["mode"]),
        "win_target": win_target_for(st["mode"]),
        "target_sr": st["target_sr"],
        "maps": maps,
    }
    buf = await asyncio.to_thread(card_renderer.generate_duel_pool_card, data, covers)
    return buf.getvalue()


def _photo(png: bytes) -> BufferedInputFile:
    return BufferedInputFile(png, filename="duel_pool.png")


async def show(
    bot: Bot, duel_id: int, tg_id: int, *,
    caption: str, keyboard: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    """Re-render the player's pool card and edit it in place (or send it fresh
    if none is tracked yet).  Best-effort: never raises.  Returns True if the
    card (and thus its keyboard) is now live for the player — callers use this
    to fall back to an auto-pick when buttons couldn't be delivered.

    Optimisation: when the visible image (order + played) is unchanged since the
    last draw, only the caption/keyboard are edited — no photo re-upload, so the
    card doesn't flicker when a turn merely attaches pick buttons."""
    st = _state(duel_id, tg_id)
    if st is None:
        return False

    key = (tuple(st["order"]), frozenset(st["played"]))
    mid = st["message_id"]
    chat_id = st["chat_id"]

    # Caption/keyboard-only edit when the picture is identical to last time.
    if mid is not None and key == st["last_key"]:
        try:
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=mid, caption=caption,
                parse_mode="HTML", reply_markup=keyboard,
            )
            return True
        except TelegramBadRequest as e:
            txt = str(e).lower()
            if "not modified" in txt:
                return True
            if not ("not found" in txt or "can't be edited" in txt
                    or "message_id_invalid" in txt):
                logger.debug(f"duel {duel_id}: pool caption edit failed: {e}")
                return False
            st["message_id"] = mid = None  # message gone → fall through to resend
        except Exception:
            logger.debug(f"duel {duel_id}: pool caption edit error", exc_info=True)
            return False

    try:
        png = await _render_png(st)
    except Exception:
        logger.warning(f"duel {duel_id}: pool card render failed for {tg_id}", exc_info=True)
        return False

    if mid is not None:
        try:
            await bot.edit_message_media(
                chat_id=chat_id, message_id=mid,
                media=InputMediaPhoto(media=_photo(png), caption=caption,
                                      parse_mode="HTML"),
                reply_markup=keyboard,
            )
            st["last_key"] = key
            return True
        except TelegramBadRequest as e:
            txt = str(e).lower()
            if "not modified" in txt:
                st["last_key"] = key
                return True
            if "not found" in txt or "can't be edited" in txt or "message_id_invalid" in txt:
                st["message_id"] = None  # gone → resend below
            else:
                logger.debug(f"duel {duel_id}: pool media edit failed, keeping card: {e}")
                return False
        except Exception:
            logger.debug(f"duel {duel_id}: pool media edit error, keeping card", exc_info=True)
            return False

    try:
        msg = await bot.send_photo(
            chat_id, _photo(png), caption=caption, parse_mode="HTML",
            reply_markup=keyboard,
        )
        st["message_id"] = msg.message_id
        st["last_key"] = key
        return True
    except Exception:
        logger.warning(f"duel {duel_id}: pool card send to {tg_id} failed", exc_info=True)
        return False


__all__ = [
    "ensure", "set_order", "mark_played", "clear", "show",
]
