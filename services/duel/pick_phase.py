"""Interactive map pick for duels.

When it's a player's turn, the round engine calls :func:`run_pick`, which DMs
that player inline buttons for each remaining map in *their own* pool and waits
up to ``PICK_TIMEOUT_SECONDS`` for a choice.  The button callback routes through
:func:`submit_pick`, which resolves the waiting future.  On timeout (or if the
DM can't be delivered) the bot auto-picks a random remaining map so the match
never stalls on an AFK player.

The pending-pick registry is in-memory only: on a restart a mid-pick round is
simply re-prompted by the recovery pass, which is acceptable for a convenience
flow.
"""

from __future__ import annotations

import asyncio
import random
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.logger import get_logger

logger = get_logger("duel.pick")

# duel_id -> {"tg_id": int, "remaining": set[int], "future": Future, "msg_id": int|None}
_pending: Dict[int, dict] = {}


def _truncate(text: str, n: int = 30) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _pick_keyboard(duel_id: int, rows: List[dict]) -> InlineKeyboardMarkup:
    # A single row of number buttons; each digit matches the map's pip on the
    # pool card and the numbered list in the prompt, so the player just taps the
    # number they want.
    row = [
        InlineKeyboardButton(
            text=str(r.get("pos") or (i + 1)),
            callback_data=f"dueld:pick:{duel_id}:{r['id']}",
        )
        for i, r in enumerate(rows)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _label_for(rows: List[dict], beatmap_id: int) -> str:
    for r in rows:
        if r["id"] == beatmap_id:
            return _truncate(str(r.get("title") or "???"), 40)
    return f"map {beatmap_id}"


async def run_pick(
    bot: Bot,
    duel_id: int,
    picker_tg_id: Optional[int],
    round_number: int,
    rows: List[dict],
    timeout_s: int,
) -> Optional[int]:
    """Prompt ``picker`` to choose one of ``rows`` (their remaining maps) in DM,
    returning the chosen ``beatmap_id``.  Auto-picks a random one on timeout or
    if no prompt can be delivered.  ``rows`` = ``[{"id","title","sr","version"}]``.
    """
    ids = [r["id"] for r in rows]
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]  # forced — no choice to make

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending[duel_id] = {"tg_id": picker_tg_id, "remaining": set(ids), "future": fut, "msg_id": None}

    msg = None
    if picker_tg_id:
        listing = "\n".join(
            f"<b>{r.get('pos') or (i + 1)}.</b> "
            f"★{float(r.get('sr') or 0.0):.1f} · {_truncate(str(r.get('title') or '???'))}"
            for i, r in enumerate(rows)
        )
        try:
            msg = await bot.send_message(
                picker_tg_id,
                f"🎯 <b>Твой ход!</b> Раунд {round_number} — выбери карту по номеру "
                f"(как на карточке пула; ⏱ {timeout_s // 60} мин, иначе бот выберет "
                f"случайную):\n\n{listing}",
                reply_markup=_pick_keyboard(duel_id, rows),
                parse_mode="HTML",
            )
            _pending[duel_id]["msg_id"] = msg.message_id
        except Exception:
            logger.debug(f"duel {duel_id}: pick prompt DM failed → auto-pick", exc_info=True)
            _pending.pop(duel_id, None)
            return random.choice(ids)

    timed_out = False
    try:
        chosen = await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError:
        chosen = random.choice(ids)
        timed_out = True
    except asyncio.CancelledError:
        chosen = random.choice(ids)
    finally:
        _pending.pop(duel_id, None)

    if msg and picker_tg_id:
        title = _label_for(rows, chosen)
        note = (f"⏱ Время вышло — выбрана случайная: <b>{title}</b>"
                if timed_out else f"✅ Выбрано: <b>{title}</b>")
        try:
            await bot.edit_message_text(
                note, chat_id=picker_tg_id, message_id=msg.message_id, parse_mode="HTML",
            )
        except Exception:
            pass
    return chosen


def submit_pick(duel_id: int, tg_id: int, beatmap_id: int) -> str:
    """Resolve a pending pick from a button press.

    Returns ``ok`` | ``not_pending`` | ``not_your_turn`` | ``invalid``.
    """
    p = _pending.get(duel_id)
    if not p:
        return "not_pending"
    if tg_id != p["tg_id"]:
        return "not_your_turn"
    if beatmap_id not in p["remaining"]:
        return "invalid"
    fut: asyncio.Future = p["future"]
    if not fut.done():
        fut.set_result(beatmap_id)
    return "ok"


def cancel_pick(duel_id: int) -> None:
    """Abort a pending pick (e.g. the duel was cancelled mid-pick)."""
    p = _pending.get(duel_id)
    if p and not p["future"].done():
        p["future"].cancel()
