import logging
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy import select

from db.models.user import User
from db.database import get_db_session
from utils.logger import get_logger
from utils.text_utils import escape_html, format_error

logger = get_logger("handlers.recent")
router = Router(name="recent")

@router.message(Command("rs", "recent"))
async def cmd_recent(message: types.Message, command: CommandObject, osu_api_client):
    tg_id = message.from_user.id
    user_input = command.args 

    target_id = None
    display_name = ""

    if not user_input:
        async with get_db_session() as session:
            stmt = select(User).where(User.telegram_id == tg_id)
            user = (await session.execute(stmt)).scalar_one_or_none()
            
            if not user or not user.osu_user_id:
                await message.answer(
                    "<b>You are not registered!</b>\n"
                    "Use <code>/register [username]</code> or specify a name: <code>/rs [username]</code>.",
                    parse_mode="HTML"
                )
                return
            target_id = user.osu_user_id
            display_name = user.osu_username
    else:
        display_name = user_input.strip()
        wait_msg = await message.answer(f"Searching for player <b>{escape_html(display_name)}</b>...", parse_mode="HTML")
        
        try:
            user_data = await osu_api_client.get_user_data(display_name)
            if not user_data:
                await wait_msg.edit_text(format_error(f"Player <b>{display_name}</b> not found."))
                return
            
            target_id = user_data.get("id")
            display_name = user_data.get("username")
        except Exception as e:
            logger.error(f"Failed to find user {display_name}: {e}")
            await wait_msg.edit_text(format_error(f"Error searching for player <b>{display_name}</b>."))
            return
    
    if 'wait_msg' not in locals():
        wait_msg = await message.answer(f"Fetching recent play for <b>{escape_html(display_name)}</b>...", parse_mode="HTML")

    try:
        logger.info(f"Fetching recent score for ID: {target_id} ({display_name})")
        recent_scores = await osu_api_client.get_user_recent_scores(target_id, limit=1)

        if not recent_scores:
            await wait_msg.edit_text(f"{escape_html(display_name)} has no recent plays in the last 24h.")
            return

        score = recent_scores[0]
        beatmap = score.get("beatmap", {})
        beatmapset = score.get("beatmapset", {})
        
        artist = beatmapset.get("artist", "Unknown")
        title = beatmapset.get("title", "Unknown")
        version = beatmap.get("version", "Unknown")
        stars = beatmap.get("difficulty_rating", 0.0)
        
        acc = score.get("accuracy", 0) * 100
        rank = score.get("rank", "F")
        pp = score.get("pp") or 0.0
        combo = score.get("max_combo", 0)
        
        mods_list = score.get("mods", [])
        mods_str = f" +{''.join(mods_list)}" if mods_list else ""
        misses = score.get("statistics", {}).get("count_miss", 0)

        lines = [
            f"🎮 <b>Recent play for {escape_html(display_name)}</b>",
            f"🎵 <b>{escape_html(artist)} - {escape_html(title)}</b>",
            f"<i>[{escape_html(version)}]</i>{mods_str} ({stars:.2f}★)",
            "═" * 25,
            f"🏅 <b>Rank:</b> {rank} | <b>Acc:</b> {acc:.2f}%",
            f"💥 <b>Combo:</b> {combo}x" + (f" ({misses}❌)" if misses else " (FC)"),
            f"🏆 <b>PP:</b> <b>{pp:.2f}pp</b>" if pp > 0 else "🏆 <b>PP:</b> —",
        ]

        cover_url = beatmapset.get("covers", {}).get("list@2x")

        if cover_url:
            await wait_msg.delete()
            await message.answer_photo(photo=cover_url, caption="\n".join(lines), parse_mode="HTML")
        else:
            await wait_msg.edit_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error fetching score for {target_id}: {e}", exc_info=True)
        await wait_msg.edit_text(format_error("Failed to fetch recent score from osu! API."))

__all__ = ["router"]
