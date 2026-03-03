# bot/handlers/profile_handlers.py
"""
Profile Handler
Commands: /profile, /refresh
Shows user stats from osu! API and HPS system.
"""

import pytz
from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select
from datetime import datetime, timedelta
from db.models.user import User
from db.database import get_db_session
from utils.logger import get_logger

router = Router()
logger = get_logger("handlers.profile")

# ← Constants
AUTO_UPDATE_HOURS = 6


def format_msk_time(dt: datetime) -> str:
    """Converts UTC datetime to MSK and formats for display."""
    if dt is None:
        return "Never"
    
    utc = pytz.UTC
    msk = pytz.timezone('Europe/Moscow')
    
    if dt.tzinfo is None:
        dt = utc.localize(dt)
    
    msk_time = dt.astimezone(msk)
    return msk_time.strftime("%d.%m.%Y %H:%M")


@router.message(Command("profile"))
async def show_profile(message: types.Message, **kwargs):
    tg_id = message.from_user.id
    api_client = kwargs.get("osu_api_client")
    
    if not api_client:
        logger.error(f"/profile failed for {tg_id}: API client not initialized")
        await message.answer("❌ Error: API client not initialized.")
        return
    
    async for session in get_db_session():
        try:
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                logger.debug(f"/profile: User {tg_id} not registered")
                await message.answer(
                    "❌ You are not registered.\n"
                    "Use `/register <osu_nickname>`",
                    parse_mode="Markdown"
                )
                return
            
            should_update = False
            now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
            last_update = user.last_api_update.replace(tzinfo=pytz.UTC) if user.last_api_update else None
            
            if last_update is None or (now_utc - last_update) > timedelta(hours=AUTO_UPDATE_HOURS):
                should_update = True
            
            if should_update:
                await message.answer("🔄 Fetching data from osu!...")
                success = await api_client.update_user_in_db(session, user)
                
                if success:
                    logger.info(f"/profile refreshed {user.osu_username} ({user.player_pp} PP)")
                    stmt = select(User).where(User.telegram_id == tg_id)
                    result = await session.execute(stmt)
                    user = result.scalar_one_or_none()
                else:
                    logger.warning(f"Failed to update {tg_id} from osu! API")
                    await message.answer("⚠️ Failed to fetch data from osu! API")
            
            # Build profile text (same as before)
            profile_text = f"👤 **Profile:** `{message.from_user.full_name}`\n"
            profile_text += "═" * 35 + "\n\n"
            profile_text += f"🎮 **osu! nickname:** `{user.osu_username}`\n"
            profile_text += f"🆔 **osu! ID:** `{user.osu_user_id}`\n"
            
            if user.player_pp > 0:
                profile_text += f"📈 **PP:** `{user.player_pp:,}`\n"
                profile_text += f"🌍 **Rank:** `#{user.global_rank:,}`\n"
                profile_text += f"🏳️ **Country:** `{user.country}`\n"
                profile_text += f"🎯 **Accuracy:** `{user.accuracy}%`\n"
                profile_text += f"🎮 **Played:** `{user.play_count:,}`\n"
            
            profile_text += "\n" + "═" * 35 + "\n\n"
            profile_text += f"🏆 **Hunter Points:** `{user.hps_points} HP`\n"
            profile_text += f"🎖️ **Rank:** `{user.rank}`\n"
            profile_text += f"📋 **Bounties participated:** `{user.bounties_participated}`\n"
            
            if user.last_api_update:
                update_time = format_msk_time(user.last_api_update)
                profile_text += f"\n🕐 **Last updated:** `{update_time}`\n"
            
            profile_text += "\n💡 Use `/refresh` to manually update data"
            
            await message.answer(profile_text, parse_mode="Markdown")
            
        except Exception as e:
            logger.error(f"Error in /profile for {tg_id}: {e}", exc_info=True)
            await message.answer("❌ An error occurred while loading profile.")
            raise


@router.message(Command("refresh"))
async def refresh_profile(message: types.Message, **kwargs):
    tg_id = message.from_user.id
    api_client = kwargs.get("osu_api_client")
    
    if not api_client:
        logger.error(f"/refresh failed for {tg_id}: API client not initialized")
        await message.answer("❌ Error: API client not initialized.")
        return
    
    async for session in get_db_session():
        try:
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                logger.debug(f"/refresh: User {tg_id} not registered")
                await message.answer(
                    "❌ You are not registered.\n"
                    "Use `/register <osu_nickname>`",
                    parse_mode="Markdown"
                )
                return
            
            await message.answer("🔄 Fetching data from osu! API...\n\n_This may take a few seconds_", parse_mode="Markdown")
            
            logger.info(f"Manual refresh triggered by user {tg_id} ({user.osu_username})")
            success = await api_client.update_user_in_db(session, user)
            
            if success:
                logger.info(f"Successfully refreshed {tg_id}: {user.player_pp} PP, #{user.global_rank} rank")
                await message.answer(
                    f"✅ **Data updated successfully!**\n\n"
                    f"📈 PP: `{user.player_pp:,}`\n"
                    f"🌍 Rank: `#{user.global_rank:,}`\n"
                    f"🎯 Accuracy: `{user.accuracy}%`",
                    parse_mode="Markdown"
                )
            else:
                logger.error(f"API returned failure for user {tg_id}")
                await message.answer("❌ Failed to update data. Please try again later.")
        
        except Exception as e:
            logger.critical(f"Unhandled exception in /refresh for {tg_id}: {e}", exc_info=True)
            await message.answer("❌ An error occurred during update. Check logs for details.")
            raise


__all__ = ["router"]
