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
    room_name = f"1984 BeatSkill Duel ({mode_label}) | {p1_username} vs {p2_username}"
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
    await irc.mp_password(channel, "")
    await asyncio.sleep(0.3)
    await irc.mp_invite(channel, p1_username)
    if not is_test:
        await asyncio.sleep(0.3)
        await irc.mp_invite(channel, p2_username)

    logger.info(f"irc_room: created room #{match_id} for duel {duel_id}")
    return match_id


async def set_map_and_start(
    irc: BanchoIRC,
    match_id: int,
    beatmap_id: int,
    countdown: int = 30,
) -> None:
    channel = f"#mp_{match_id}"
    await irc.mp_map(channel, beatmap_id, mode=0)
    await asyncio.sleep(0.3)
    await irc.mp_mods(channel, "NF")
    await asyncio.sleep(0.3)
    await irc.mp_start(channel, countdown)
    logger.info(f"irc_room: set map {beatmap_id} and starting in {countdown}s (match {match_id})")


async def close_room(irc: BanchoIRC, match_id: int) -> None:
    channel = f"#mp_{match_id}"
    await irc.mp_close(channel)
    logger.info(f"irc_room: closed room #{match_id}")
