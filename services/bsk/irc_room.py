"""IRC-based multiplayer room management for BSK duels."""

import asyncio
from typing import Optional

from services.bancho_irc import BanchoIRC, get_irc_client
from utils.logger import get_logger

logger = get_logger("bsk.irc_room")


async def create_duel_room(
    irc: BanchoIRC,
    duel_id: int,
    p1_username: str,
    p2_username: str,
    mode: str = "casual",
    is_test: bool = False,
) -> Optional[int]:
    mode_label = mode.upper()
    room_name = f"1984 BeatSkill Duel ({mode_label}) | #{duel_id}"
    match_id = await irc.mp_make(room_name)
    if not match_id:
        logger.warning(f"irc_room: failed to create room for duel {duel_id}")
        return None

    channel = f"#mp_{match_id}"
    await irc.join_channel(channel)
    await asyncio.sleep(0.5)
    size = 1 if is_test else 2
    await irc.mp_set(channel, team_mode=0, score_mode=0, size=size)
    await asyncio.sleep(0.3)
    await irc.mp_invite(channel, p1_username)
    if not is_test:
        await asyncio.sleep(0.3)
        await irc.mp_invite(channel, p2_username)

    logger.info(f"irc_room: created room #{match_id} for duel {duel_id}")

    players = [p1_username] if is_test else [p1_username, p2_username]
    _start_rejoin_watcher(irc, match_id, duel_id, players)

    return match_id


def _start_rejoin_watcher(
    irc: BanchoIRC, match_id: int, duel_id: int, players: list[str],
) -> None:
    """Watch for player-left events and re-invite once per leave."""
    channel = f"#mp_{match_id}"
    player_set = {p.lower() for p in players}

    async def _on_player_left(ch: str, username: str):
        if ch != channel:
            return
        if username.lower() not in player_set:
            return

        await asyncio.sleep(5)

        from sqlalchemy import select
        from db.database import get_db_session
        from db.models.bsk_duel import BskDuel

        async with get_db_session() as session:
            duel = (await session.execute(
                select(BskDuel).where(BskDuel.id == duel_id)
            )).scalar_one_or_none()
            if not duel or duel.status in ('finished', 'cancelled'):
                return

        if irc.connected:
            try:
                await irc.mp_invite(channel, username)
                logger.info(f"irc_room: re-invited {username} to #{match_id}")
            except Exception as e:
                logger.warning(f"irc_room: re-invite failed for {username}: {e}")

    irc.on("player_left", _on_player_left)


async def set_map_and_start(
    irc: BanchoIRC,
    match_id: int,
    beatmap_id: int,
    countdown: int = 90,
) -> None:
    channel = f"#mp_{match_id}"
    await irc.mp_map(channel, beatmap_id, mode=0)
    await asyncio.sleep(0.3)
    await irc.mp_mods(channel, "NF")
    await asyncio.sleep(0.3)

    all_ready = asyncio.Event()

    async def _on_ready(ch: str, text: str):
        if ch == channel:
            all_ready.set()

    irc.on("all_ready", _on_ready)
    logger.info(f"irc_room: set map {beatmap_id}, waiting for ready or {countdown}s (match {match_id})")

    try:
        await asyncio.wait_for(all_ready.wait(), timeout=countdown)
        await irc.mp_start(channel, 10)
        logger.info(f"irc_room: all ready, starting in 10s (match {match_id})")
    except asyncio.TimeoutError:
        await irc.mp_start(channel, 10)
        logger.info(f"irc_room: timeout reached, force starting in 10s (match {match_id})")
    finally:
        try:
            irc._handlers.get("all_ready", []).remove(_on_ready)
        except ValueError:
            pass


async def close_room(irc: BanchoIRC, match_id: int) -> None:
    channel = f"#mp_{match_id}"
    await irc.mp_close(channel)
    logger.info(f"irc_room: closed room #{match_id}")
