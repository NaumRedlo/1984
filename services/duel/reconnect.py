"""Mid-round Bancho disconnect tracking for duels.

When a duel player drops out of the multiplayer lobby (a network hiccup), the
round engine pauses the round, aborts the map, and waits for them to come back.
This module is the *passive* half of that: it listens to BanchoBot's
``player_left`` / ``player_joined`` events on a duel's channel and tracks, per
duel, which of the two players are currently missing — exposing asyncio Events
the engine awaits.  The *active* half (abort, re-invite cadence, auto-cancel)
lives in :mod:`services.duel.round_engine`.

State is in-memory and best-effort, keyed by ``duel_id`` (mirrors how
``pick_phase`` and ``status_card`` keep their live state).  After a process
restart the engine re-arms via :func:`arm` when it resumes a duel.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Set

from utils.logger import get_logger

logger = get_logger("duel.reconnect")


class _DuelConn:
    __slots__ = ("players", "missing", "gone", "back")

    def __init__(self) -> None:
        self.players: Set[str] = set()   # lowercased osu! usernames in this duel
        self.missing: Set[str] = set()   # subset currently absent from the lobby
        # ``gone`` is set while at least one player is missing; ``back`` is set
        # while everyone is present.  They are mutually exclusive mirrors so the
        # engine can await either edge without polling.
        self.gone = asyncio.Event()
        self.back = asyncio.Event()
        self.back.set()


_state: Dict[int, _DuelConn] = {}


def _get(duel_id: int) -> _DuelConn:
    st = _state.get(duel_id)
    if st is None:
        st = _DuelConn()
        _state[duel_id] = st
    return st


def mark_left(duel_id: int, username: str) -> None:
    st = _get(duel_id)
    st.missing.add(username.lower())
    st.back.clear()
    st.gone.set()


def mark_joined(duel_id: int, username: str) -> None:
    st = _get(duel_id)
    st.missing.discard(username.lower())
    if not st.missing:
        st.gone.clear()
        st.back.set()


def missing(duel_id: int) -> Set[str]:
    st = _state.get(duel_id)
    return set(st.missing) if st else set()


def gone_event(duel_id: int) -> asyncio.Event:
    """Set while someone is missing (the engine watches this to interrupt a round)."""
    return _get(duel_id).gone


def back_event(duel_id: int) -> asyncio.Event:
    """Set while everyone is present (the engine awaits this during the grace window)."""
    return _get(duel_id).back


def clear(duel_id: int) -> None:
    _state.pop(duel_id, None)


def arm(irc, duel_id: int, match_id: int, players: list[str]) -> None:
    """(Re)register ``player_left`` / ``player_joined`` listeners for a duel's
    channel.  Idempotent: existing reconnect listeners on the channel are dropped
    first, so it is safe to call from both room creation and the post-reconnect
    re-join pass without stacking duplicates."""
    channel = f"#mp_{match_id}"
    irc.drop_channel_handlers(channel, event="player_left")
    irc.drop_channel_handlers(channel, event="player_joined")

    pset = {p.lower() for p in players if p}
    st = _get(duel_id)
    st.players = pset

    async def _on_left(_ch: str, username: str) -> None:
        if username.lower() in pset:
            mark_left(duel_id, username)
            logger.info(f"duel {duel_id}: {username} left the lobby (#{match_id})")

    async def _on_joined(_ch: str, username: str) -> None:
        if username.lower() in pset:
            mark_joined(duel_id, username)
            logger.info(f"duel {duel_id}: {username} rejoined the lobby (#{match_id})")

    irc.on("player_left", _on_left, channel=channel)
    irc.on("player_joined", _on_joined, channel=channel)
