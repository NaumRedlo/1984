import asyncio
from typing import Any, Optional

from db.database import AsyncSessionFactory
from services.render_farm import skins
from services.render_farm.queue import RIVALS, chat_rivals_of, rivals_of
from utils.logger import get_logger
from utils.osr import Header
from utils.ttl_cache import TTLCache

logger = get_logger("services.render_farm.orders")

GLOBAL_TOP_KEEP = 600.0
_global_tops = TTLCache(maxsize=2000, ttl=GLOBAL_TOP_KEEP)

async def board_chat(telegram_id: int, chat_id: int) -> int:
    from db.models.chat_member import ChatMember
    from services.render_farm import players
    from sqlalchemy import select

    if not telegram_id:
        return chat_id
    async with AsyncSessionFactory() as session:
        pinned = await players.pinned_chat(session, telegram_id)
        player = await players._player_of(session, telegram_id)
        if pinned is not None and player is not None:
            held = await session.scalar(select(ChatMember.id).where(ChatMember.player_id == player.id, ChatMember.chat_id == pinned))
            if held is not None:
                return pinned
    return chat_id

SCORES_WAIT = 4.0
_refreshing: dict[tuple[int, int], asyncio.Task] = {}

async def _sync_scores(osu_api_client, beatmap_id: int, chat_id: int) -> None:
    from services.leaderboard.service import _sync_beatmap_scores

    try:
        async with AsyncSessionFactory() as session:
            await _sync_beatmap_scores(session, osu_api_client, beatmap_id, chat_id)
    except Exception as exc:
        logger.info("scores of map %s in %s were not refreshed: %s", beatmap_id, chat_id, exc)

async def _fresh_scores(osu_api_client, beatmap_id: int, chat_id: int, wait: float = SCORES_WAIT) -> None:
    if osu_api_client is None:
        return
    key = (beatmap_id, chat_id)
    task = _refreshing.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_sync_scores(osu_api_client, beatmap_id, chat_id))
        _refreshing[key] = task
        task.add_done_callback(lambda done: _refreshing.pop(key, None) if _refreshing.get(key) is done else None)
    try:
        await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.TimeoutError:
        logger.info("scores of map %s in %s are still coming, the render takes what is known", beatmap_id, chat_id)

async def rivals_for(osu_api_client, beatmap: Optional[dict], head: Header, telegram_id: int, chat_id: int,
                     board: Optional[str] = None) -> list[dict[str, Any]]:
    beatmap_id = int((beatmap or {}).get("id") or 0)
    if not beatmap_id:
        return []
    if (board or skins.board_for(telegram_id)) == "chat" and head.mode == 0:
        chat_id = await board_chat(telegram_id, chat_id)
    if (board or skins.board_for(telegram_id)) == "chat" and chat_id < 0 and head.mode == 0:
        try:
            from services.render_farm import maps

            await _fresh_scores(osu_api_client, beatmap_id, chat_id)
            async with AsyncSessionFactory() as session:
                shown = await maps.board(session, osu_api_client, beatmap_id, chat_id, telegram_id, sync=False)
            chat = chat_rivals_of(shown.get("rows"))
            if chat:
                return chat
        except Exception as exc:
            logger.info("no chat leaderboard for %s in %s: %s", beatmap_id, chat_id, exc)
    if osu_api_client is None:
        return []
    key = (beatmap_id, head.mode)
    kept = _global_tops.get(key)
    if kept is not None:
        return kept
    try:
        top = rivals_of(await osu_api_client.get_beatmap_scores(beatmap_id, limit=RIVALS, mode="mania" if head.mode == 3 else "osu"))
        _global_tops[key] = top
        return top
    except Exception as exc:
        logger.info("no leaderboard for %s: %s", head.beatmap_md5, exc)
        return []
