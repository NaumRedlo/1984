"""In-memory state storage for BSK duel phases.

This module centralizes transient duel state that currently lives in the duel
manager. The data is intentionally process-local and is not persisted across
bot restarts.

The next refactoring step can switch duel_manager.py to import these storages
instead of keeping its own module-level dictionaries.
"""

from typing import Any

# ── In-memory pool state (persists across rounds within one pool) ────────────
# Keyed by duel_id. Lives from ban-resolve until pool exhausted / duel cancelled.
pool_state: dict[int, dict[str, Any]] = {}

# ── In-memory ban-phase state ─────────────────────────────────────────────────
# Keyed by duel_id. Cleared when bans resolve or on cancel.
#
# Expected structure:
#   p1_tg_id, p2_tg_id      int | None  — Telegram IDs
#   p1_dm_msg, p2_dm_msg    int | None  — message IDs of ban DM cards
#   p1_bans, p2_bans        list[int]   — beatmap_ids selected for ban
#   p1_ready, p2_ready      bool
#   dm_candidates_p1/p2     list[dict]  — full map data for DM card renders
#   group_candidates_p1/p2  list[dict]  — thin map data for group card renders
#   round_num               int
#   p1_name, p2_name        str
#   p1_country, p2_country  str
#   p1_priority             bool
#   is_test                 bool
ban_state: dict[int, dict[str, Any]] = {}


def clear_duel_state(duel_id: int) -> None:
    """Remove all transient state for a duel."""
    pool_state.pop(duel_id, None)
    ban_state.pop(duel_id, None)


def has_pool_state(duel_id: int) -> bool:
    """Return True if a duel has active pool state."""
    return duel_id in pool_state


def has_ban_state(duel_id: int) -> bool:
    """Return True if a duel has active ban-phase state."""
    return duel_id in ban_state
