"""Single entry point for tearing down a duel's in-memory state.

A duel keeps best-effort live state across several modules — the live status
card (:mod:`status_card`), a pending interactive pick (:mod:`pick_phase`),
disconnect tracking (:mod:`reconnect`), and the engine's task de-dupe set
(:mod:`round_engine`).  When a duel is cancelled / force-closed we want to drop
all of it in one call.  Best-effort: every step is independent and never raises,
so a half-initialised duel is torn down cleanly.
"""

from __future__ import annotations

from utils.logger import get_logger

logger = get_logger("duel.state")


def clear_duel_state(duel_id: int) -> None:
    """Forget every in-memory trace of a duel. Safe to call repeatedly and on a
    duel that has no live state."""
    try:
        from services.duel import pick_phase
        pick_phase.cancel_pick(duel_id)  # unblocks the engine if it awaits a pick
    except Exception:
        logger.debug(f"clear_duel_state({duel_id}): pick_phase", exc_info=True)
    try:
        from services.duel import pool_swap
        pool_swap.cancel_swap(duel_id)  # unblocks both players' swap DMs, if open
    except Exception:
        logger.debug(f"clear_duel_state({duel_id}): pool_swap", exc_info=True)
    try:
        from services.duel import status_card
        status_card.clear(duel_id)
    except Exception:
        logger.debug(f"clear_duel_state({duel_id}): status_card", exc_info=True)
    try:
        from services.duel import reconnect
        reconnect.clear(duel_id)
    except Exception:
        logger.debug(f"clear_duel_state({duel_id}): reconnect", exc_info=True)
    try:
        from services.duel import round_engine
        round_engine._active.discard(duel_id)
    except Exception:
        logger.debug(f"clear_duel_state({duel_id}): round_engine", exc_info=True)
