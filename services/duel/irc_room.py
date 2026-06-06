"""IRC-based multiplayer room management for DUEL duels."""

import asyncio
from typing import Optional

from services.bancho_irc import BanchoIRC, get_irc_client
from utils.logger import get_logger

logger = get_logger("duel.irc_room")


async def create_duel_room(
    irc: BanchoIRC,
    duel_id: int,
    p1_username: str,
    p2_username: str,
    mode: str = "casual",
    is_test: bool = False,
) -> Optional[int]:
    mode_label = mode.upper()
    room_name = f"1984 Duel ({mode_label}) | #{duel_id}"
    match_id = await irc.mp_make(room_name)
    if not match_id:
        logger.warning(f"irc_room: failed to create room for duel {duel_id}")
        return None

    channel = f"#mp_{match_id}"
    await irc.join_channel(channel)
    await asyncio.sleep(0.5)
    size = 1 if is_test else 2
    # Head-to-head, ScoreV2 (score_mode=3), `size` players.
    await irc.mp_set(channel, team_mode=0, score_mode=3, size=size)
    await asyncio.sleep(0.3)
    # Freemod once, at room creation — players keep their own HD/HR/DT across
    # rounds. Per-round re-setting spammed "!mp mods Freemod" in the channel
    # for no benefit; Bancho remembers the setting for the room lifetime.
    await irc.mp_mods(channel, "Freemod")
    await asyncio.sleep(0.3)
    await irc.mp_invite(channel, p1_username)
    if not is_test:
        await asyncio.sleep(0.3)
        await irc.mp_invite(channel, p2_username)

    logger.info(f"irc_room: created room #{match_id} for duel {duel_id}")

    players = [p1_username] if is_test else [p1_username, p2_username]
    from services.duel import reconnect
    reconnect.arm(irc, duel_id, match_id, players)

    return match_id


async def set_map_and_start(
    irc: BanchoIRC,
    match_id: int,
    beatmap_id: int,
    countdown: int = 90,
) -> None:
    channel = f"#mp_{match_id}"
    await irc.mp_map(channel, beatmap_id, mode=0)
    await asyncio.sleep(0.3)
    # Freemod is set once in create_duel_room — Bancho keeps it for the room
    # lifetime, so re-sending it every round would only spam the channel.
    # Round results flow through osu_api.get_match → round_engine._decide_round
    # (hardcore: a failed — or NoFail — pass scores nothing; among legitimate
    # passers the higher score wins, mod-normalised in ranked).

    all_ready = asyncio.Event()

    async def _on_ready(ch: str, text: str):
        all_ready.set()

    irc.on("all_ready", _on_ready, channel=channel)
    logger.info(f"irc_room: set map {beatmap_id}, waiting for ready or {countdown}s (match {match_id})")

    try:
        await asyncio.wait_for(all_ready.wait(), timeout=countdown)
        await irc.mp_start(channel, 10)
        logger.info(f"irc_room: all ready, starting in 10s (match {match_id})")
    except asyncio.TimeoutError:
        await irc.mp_start(channel, 10)
        logger.info(f"irc_room: timeout reached, force starting in 10s (match {match_id})")
    finally:
        irc.off("all_ready", _on_ready, channel=channel)


async def rejoin_active_duel_channels() -> None:
    """After an IRC (re)connect, JOIN every multiplayer channel that belongs
    to a duel still in progress, and re-arm the rejoin watcher. Existing
    per-channel handlers in BanchoIRC._handlers survive across reconnects, so
    JOINing is enough to restart the event flow."""
    from sqlalchemy import select
    from db.database import get_db_session
    from db.models.duel import Duel
    from db.models.user import User

    irc = get_irc_client()
    if not irc.connected:
        return

    async with get_db_session() as session:
        duels = (await session.execute(
            select(Duel).where(
                Duel.status.in_(['accepted', 'round_active']),
                Duel.osu_match_id.is_not(None),
            )
        )).scalars().all()
        snapshots = []
        for d in duels:
            try:
                match_id = int(d.osu_match_id)
            except (TypeError, ValueError):
                continue
            p1 = (await session.execute(
                select(User).where(User.id == d.player1_user_id)
            )).scalar_one_or_none()
            p2 = (await session.execute(
                select(User).where(User.id == d.player2_user_id)
            )).scalar_one_or_none()
            players = []
            if p1 and p1.osu_username:
                players.append(p1.osu_username)
            if p2 and p2.osu_username:
                players.append(p2.osu_username)
            snapshots.append((d.id, match_id, players))

    if not snapshots:
        return

    logger.info(f"irc_room: rejoining {len(snapshots)} active duel channel(s) after reconnect")
    for duel_id, match_id, players in snapshots:
        channel = f"#mp_{match_id}"
        try:
            await irc.join_channel(channel)
        except Exception as e:
            logger.warning(f"irc_room: rejoin {channel} failed: {e}")
            continue
        # Re-arm disconnect tracking (idempotent — arm() drops the channel's
        # prior reconnect listeners first, so reconnects don't stack handlers).
        # all_ready/match_finished listeners are owned by the live engine task
        # and resume firing once events arrive on the rejoined channel.
        if players:
            from services.duel import reconnect
            reconnect.arm(irc, duel_id, match_id, players)


async def close_room(irc: BanchoIRC, match_id: int) -> None:
    channel = f"#mp_{match_id}"
    try:
        await irc.mp_close(channel)
    except Exception as e:
        logger.warning(f"irc_room: mp_close failed for #{match_id}: {e}")
    # Drop every handler bound to this channel — prevents listener leaks even
    # if some caller forgot its own off() in a finally.
    irc.drop_channel_handlers(channel)
    logger.info(f"irc_room: closed room #{match_id}")
