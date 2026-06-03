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


def _truncate(text: str, n: int = 30) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _swap_keyboard(duel_id: int, pool: List[int],
                   rows_by_id: Dict[int, dict], remaining_swaps: int) -> InlineKeyboardMarkup:
    buttons = []
    for bid in pool:
        r = rows_by_id.get(bid) or {}
        sr = float(r.get("sr") or 0.0)
        label = f"★{sr:.1f} · {_truncate(str(r.get('title') or '???'))}"
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"dueld:swap:{duel_id}:{bid}",
        )])
    buttons.append([InlineKeyboardButton(
        text=("✅ Готово" if remaining_swaps < MAX_SWAPS else "✅ Оставить пул как есть"),
        callback_data=f"dueld:swapdone:{duel_id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _header(remaining_swaps: int, timeout_s: int) -> str:
    used = MAX_SWAPS - remaining_swaps
    return (
        f"🔁 <b>Подгонка пула</b>\n"
        f"Можно заменить до <b>{MAX_SWAPS}</b> карт перед началом дуэли.\n"
        f"Замен использовано: <b>{used}/{MAX_SWAPS}</b> · ⏱ {timeout_s // 60} мин."
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

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    excluded: set[int] = set(all_other_ids) | set(pool)
    key = (duel_id, picker_tg_id)
    _pending[key] = {
        "tg_id": picker_tg_id,
        "msg_id": None,
        "future": fut,
        "pool": pool,
        "remaining_swaps": MAX_SWAPS,
        "rows_by_id": dict(rows_by_id),
        "excluded": excluded,
        "fetch_candidate": fetch_candidate,
        "bot": bot,
    }

    try:
        msg = await bot.send_message(
            picker_tg_id,
            _header(MAX_SWAPS, timeout_s),
            parse_mode="HTML",
            reply_markup=_swap_keyboard(duel_id, pool, rows_by_id, MAX_SWAPS),
        )
        _pending[key]["msg_id"] = msg.message_id
    except Exception:
        logger.debug(f"duel {duel_id}: swap prompt DM failed → skipping swap", exc_info=True)
        _pending.pop(key, None)
        return pool

    try:
        await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        st = _pending.pop(key, None)
        final_pool = st["pool"] if st else pool

    # Close out the DM with a confirmation note.
    try:
        await bot.edit_message_text(
            f"✅ Пул зафиксирован — поехали в дуэль.",
            chat_id=picker_tg_id, message_id=msg.message_id, parse_mode="HTML",
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

    # Re-render the DM with the swapped pool.
    msg_id = p.get("msg_id")
    if msg_id:
        try:
            await p["bot"].edit_message_text(
                _header(p["remaining_swaps"], SWAP_TIMEOUT_SECONDS),
                chat_id=tg_id, message_id=msg_id, parse_mode="HTML",
                reply_markup=_swap_keyboard(duel_id, p["pool"], p["rows_by_id"],
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
