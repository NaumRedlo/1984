"""Pre-round-1 map-pool swap window.

After the room is built and before the first interactive pick, each player gets
a short DM window to swap up to ``MAX_SWAPS`` cards from their personal 6-map
pool.  Tapping a card replaces it with a freshly-rolled candidate from the same
SR window, excluding every map already assigned to either pool (so the two
players still never see overlap).

State is in-memory, keyed by ``duel_id`` (mirrors :mod:`pick_phase` /
:mod:`reconnect` / :mod:`status_card`).  On bot restart any in-flight swap is
simply forgotten — :mod:`round_engine` only invokes us on a cold start before
the main loop, so a restart-during-swap just means the players keep whatever
pool was last persisted, which is correct.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.logger import get_logger

logger = get_logger("duel.swap")

# How many of their 6 maps a player may reroll in this window.
MAX_SWAPS = 3
# Seconds before the window closes and the current pool is locked in.
SWAP_TIMEOUT_SECONDS = 60

# (duel_id, tg_id) -> {...}.  Keyed by pair because both players in a duel
# swap in parallel, so duel_id alone would collide.
_pending: Dict[tuple[int, int], dict] = {}


def _by_duel(duel_id: int) -> list[tuple[int, int]]:
    return [k for k in list(_pending.keys()) if k[0] == duel_id]


def _swap_keyboard(duel_id: int, pool: List[int],
                   rows_by_id: Dict[int, dict], remaining_swaps: int) -> InlineKeyboardMarkup:
    # One row of number buttons matching the numbered pool list in the header;
    # tapping a number replaces that card. Done button on its own row below.
    row = [
        InlineKeyboardButton(
            text=str(i + 1), callback_data=f"dueld:swap:{duel_id}:{bid}",
        )
        for i, bid in enumerate(pool)
    ]
    done = [InlineKeyboardButton(
        text=("✅ Готово" if remaining_swaps < MAX_SWAPS else "✅ Оставить пул как есть"),
        callback_data=f"dueld:swapdone:{duel_id}",
    )]
    return InlineKeyboardMarkup(inline_keyboard=[row, done])


def _swap_caption(remaining_swaps: int, timeout_s: int) -> str:
    # The 6 maps are visible on the card itself, so the caption stays short —
    # just the action and the swap budget; the numbered buttons match the pips.
    used = MAX_SWAPS - remaining_swaps
    return (
        f"🔁 <b>Подгонка пула</b> — жми номер карты на клавиатуре, чтобы заменить "
        f"её свежей под тот же уровень (до <b>{MAX_SWAPS}</b> замен перед стартом).\n"
        f"Замен: <b>{used}/{MAX_SWAPS}</b> · ⏱ {timeout_s // 60} мин."
    )


async def run_swap(
    bot: Bot,
    duel_id: int,
    picker_tg_id: Optional[int],
    pool: List[int],
    rows_by_id: Dict[int, dict],
    all_other_ids: List[int],
    fetch_candidate,  # async () -> Optional[dict({"id","title","sr","version"})]
    timeout_s: int = SWAP_TIMEOUT_SECONDS,
) -> List[int]:
    """Open a swap DM for ``picker_tg_id`` over their ``pool``; return the
    (possibly modified) ``pool`` once the player taps ✅ or the timer expires.

    ``fetch_candidate`` is supplied by the caller — it knows the target SR and
    the full set of already-taken ids (both players' pools + any rejected
    candidates).  Returning ``None`` from it means "no fresh map available" and
    the swap is silently rejected for that tap; the player can pick another.
    """
    pool = list(pool)
    if not picker_tg_id or not pool:
        return pool

    from services.duel import pool_card

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    excluded: set[int] = set(all_other_ids) | set(pool)
    key = (duel_id, picker_tg_id)
    _pending[key] = {
        "tg_id": picker_tg_id,
        "future": fut,
        "pool": pool,
        "remaining_swaps": MAX_SWAPS,
        "rows_by_id": dict(rows_by_id),
        "excluded": excluded,
        "fetch_candidate": fetch_candidate,
        "bot": bot,
    }

    # Open the swap window ON the player's live pool card (buttons + caption),
    # rather than a separate text prompt.
    await pool_card.show(
        bot, duel_id, picker_tg_id,
        caption=_swap_caption(MAX_SWAPS, timeout_s),
        keyboard=_swap_keyboard(duel_id, pool, rows_by_id, MAX_SWAPS),
    )

    try:
        await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        st = _pending.pop(key, None)
        final_pool = st["pool"] if st else pool

    # Lock the pool in: drop the swap keyboard, idle caption.
    try:
        await pool_card.show(
            bot, duel_id, picker_tg_id,
            caption="✅ <b>Пул зафиксирован</b> — ждём начала раунда…",
            keyboard=None,
        )
    except Exception:
        pass
    return final_pool


async def submit_swap(duel_id: int, tg_id: int, beatmap_id: int) -> str:
    """Handle a 'replace this card' button press from the swap keyboard.

    Returns ``ok`` | ``not_pending`` | ``not_your_turn`` | ``invalid`` |
    ``out_of_swaps`` | ``no_candidate``.
    """
    p = _pending.get((duel_id, tg_id))
    if not p:
        # No swap window open for *this* player — but the other player in the
        # same duel might still be swapping; treat it as "not yours".
        return "not_your_turn" if _by_duel(duel_id) else "not_pending"
    if beatmap_id not in p["pool"]:
        return "invalid"
    if p["remaining_swaps"] <= 0:
        return "out_of_swaps"

    try:
        new = await p["fetch_candidate"](p["excluded"])
    except Exception:
        logger.debug(f"duel {duel_id}: swap fetch_candidate raised", exc_info=True)
        new = None
    if not new:
        return "no_candidate"

    new_bid = int(new["id"])
    p["excluded"].add(new_bid)
    idx = p["pool"].index(beatmap_id)
    p["pool"][idx] = new_bid
    p["rows_by_id"][new_bid] = new
    p["remaining_swaps"] -= 1

    # Re-render the card with the swapped pool (new map + updated swap budget).
    from services.duel import pool_card
    pool_card.set_order(duel_id, tg_id, p["pool"])
    try:
        await pool_card.show(
            p["bot"], duel_id, tg_id,
            caption=_swap_caption(p["remaining_swaps"], SWAP_TIMEOUT_SECONDS),
            keyboard=_swap_keyboard(duel_id, p["pool"], p["rows_by_id"],
                                    p["remaining_swaps"]),
        )
    except Exception:
        logger.debug(f"duel {duel_id}: swap re-render failed", exc_info=True)

    # Auto-finish when all swaps are spent — no point keeping the DM open.
    if p["remaining_swaps"] <= 0:
        fut: asyncio.Future = p["future"]
        if not fut.done():
            fut.set_result(True)
    return "ok"


def submit_done(duel_id: int, tg_id: int) -> str:
    """Handle the ✅ button — finalise the current pool early."""
    p = _pending.get((duel_id, tg_id))
    if not p:
        return "not_your_turn" if _by_duel(duel_id) else "not_pending"
    fut: asyncio.Future = p["future"]
    if not fut.done():
        fut.set_result(True)
    return "ok"


def cancel_swap(duel_id: int) -> None:
    """Abort every pending swap window for a duel (e.g. the duel was cancelled
    mid-swap, so both players' DM prompts need to unblock)."""
    for key in _by_duel(duel_id):
        p = _pending.get(key)
        if p and not p["future"].done():
            p["future"].cancel()
