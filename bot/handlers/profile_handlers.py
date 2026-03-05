import pytz
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select

from db.models.user import User
from db.database import get_db_session
from utils.logger import get_logger
from utils.hp_calculator import get_rank_for_hp, get_next_rank_info

router = Router(name="profile")
logger = get_logger("handlers.profile")

AUTO_UPDATE_HOURS = 6


def format_msk_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "Never"
    
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    msk = pytz.timezone('Europe/Moscow')
    msk_time = dt.astimezone(msk)
    return msk_time.strftime("%d.%m.%Y %H:%M")


@router.message(Command("profile"))
async def show_profile(message: types.Message, osu_api_client):
    tg_id = message.from_user.id
    
    if not osu_api_client:
        logger.error(f"/profile failed for {tg_id}: API client not initialized")
        await message.answer("Error: API client not initialized.")
        return
    
    async with get_db_session() as session:
        try:
            stmt = select(User).where(User.telegram_id == tg_id)
            user = (await session.execute(stmt)).scalar_one_or_none()
            
            if not user:
                logger.debug(f"/profile: User {tg_id} not registered")
                await message.answer(
                    "You are not registered.\n"
                    "Use <code>/register &lt;osu_nickname&gt;</code>",
                    parse_mode="HTML"
                )
                return
            
            should_update = False
            now_utc = datetime.now(timezone.utc)
            last_update = user.last_api_update.replace(tzinfo=timezone.utc) if user.last_api_update else None
            
            if last_update is None or (now_utc - last_update) > timedelta(hours=AUTO_UPDATE_HOURS):
                should_update = True
            
            if should_update:
                wait_msg = await message.answer("Fetching fresh data from osu!...")
                success = await osu_api_client.update_user_in_db(session, user)
                
                if success:
                    logger.info(f"/profile refreshed {user.osu_username} ({user.player_pp} PP)")
                    await session.refresh(user)
                    await wait_msg.delete()
                else:
                    logger.warning(f"Failed to update {tg_id} from osu! API")
                    await wait_msg.edit_text("Failed to fetch data from osu! API. Showing cached data.")
            
            profile_text = (
                f"👤 <b>Profile:</b> {message.from_user.full_name}\n"
                f"{'═' * 35}\n\n"
                f"🎮 <b>osu! nickname:</b> <code>{user.osu_username}</code>\n"
                f"🆔 <b>osu! ID:</b> <code>{user.osu_user_id}</code>\n"
            )
            
            if user.player_pp and user.player_pp > 0:
                profile_text += (
                    f"📈 <b>PP:</b> <code>{user.player_pp:,}</code>\n"
                    f"🌍 <b>Rank:</b> <code>#{user.global_rank:,}</code>\n"
                    f"🏳️ <b>Country:</b> <code>{user.country}</code>\n"
                    f"🎯 <b>Accuracy:</b> <code>{user.accuracy}%</code>\n"
                    f"🎮 <b>Played:</b> <code>{user.play_count:,}</code>\n"
                )
            
            hp = user.hps_points or 0
            rank_info = get_next_rank_info(hp)
            current_rank = rank_info["current"]

            update_time = format_msk_time(user.last_api_update)
            profile_text += (
                f"\n{'═' * 35}\n\n"
                f"🏆 <b>Hunter Points:</b> <code>{hp} HP</code>\n"
                f"🎖️ <b>Rank:</b> <code>{current_rank}</code>\n"
            )

            if rank_info["next"]:
                profile_text += (
                    f"📊 <b>Next rank:</b> <code>{rank_info['next']}</code> "
                    f"(<code>{rank_info['hp_needed']} HP</code> to go)\n"
                )

            profile_text += (
                f"📋 <b>Bounties participated:</b> <code>{user.bounties_participated or 0}</code>\n\n"
                f"🕐 <b>Last updated:</b> <code>{update_time}</code>\n\n"
                f"💡 <i>Use /refresh to manually update data</i>"
            )
            
            await message.answer(profile_text, parse_mode="HTML")
            
        except Exception as e:
            logger.error(f"Error in /profile for {tg_id}: {e}", exc_info=True)
            await message.answer("An error occurred while loading profile.")


@router.message(Command("refresh"))
async def refresh_profile(message: types.Message, osu_api_client):
    tg_id = message.from_user.id
    
    if not osu_api_client:
        logger.error(f"/refresh failed for {tg_id}: API client not initialized")
        await message.answer("Error: API client not initialized.")
        return
    
    async with get_db_session() as session:
        try:
            stmt = select(User).where(User.telegram_id == tg_id)
            user = (await session.execute(stmt)).scalar_one_or_none()
            
            if not user:
                logger.debug(f"/refresh: User {tg_id} not registered")
                await message.answer(
                    "You are not registered.\n"
                    "Use <code>/register &lt;osu_nickname&gt;</code>",
                    parse_mode="HTML"
                )
                return
            
            wait_msg = await message.answer(
                "Fetching data from osu! API...\n\n<i>This may take a few seconds</i>", 
                parse_mode="HTML"
            )
            
            logger.info(f"Manual refresh triggered by user {tg_id} ({user.osu_username})")
            success = await osu_api_client.update_user_in_db(session, user)
            
            if success:
                await session.refresh(user)
                logger.info(f"Successfully refreshed {tg_id}: {user.player_pp} PP, #{user.global_rank} rank")
                await wait_msg.edit_text(
                    f"✅ <b>Data updated successfully!</b>\n\n"
                    f"📈 <b>PP:</b> <code>{user.player_pp:,}</code>\n"
                    f"🌍 <b>Rank:</b> <code>#{user.global_rank:,}</code>\n"
                    f"🎯 <b>Accuracy:</b> <code>{user.accuracy}%</code>",
                    parse_mode="HTML"
                )
            else:
                logger.error(f"API returned failure for user {tg_id}")
                await wait_msg.edit_text("Failed to update data. Please try again later.", parse_mode="HTML")
        
        except Exception as e:
            logger.error(f"Unhandled exception in /refresh for {tg_id}: {e}", exc_info=True)
            error_text = "An error occurred during update. Check logs for details."
            try:
                await wait_msg.edit_text(error_text)
            except NameError:
                await message.answer(error_text)

__all__ = ["router"]
