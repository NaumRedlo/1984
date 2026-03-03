from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select
from db.models.user import User
from db.database import get_db_session
from datetime import datetime, timedelta
import pytz
from utils.osu_api_client import OsuApiClient

router = Router()

AUTO_UPDATE_HOURS = 0.5


@router.message(Command("profile"))
async def show_profile(message: types.Message, **kwargs):
    """
    Shows an extended user profile with auto-refresh.
    """
    tg_id = message.from_user.id
    
    # Get API client
    api_client = kwargs.get("osu_api_client")
    if not api_client:
        await message.answer("❌ Error: API client not initialized.")
        return

    async for session in get_db_session():
        try:
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                await message.answer(
                    "❌ You are not registered.\n"
                    "Use `/register <nickname>`",
                    parse_mode="Markdown"
                )
                return

            should_update = False
            
            if user.last_api_update is None:
                should_update = True
            elif datetime.utcnow() - user.last_api_update > timedelta(hours=AUTO_UPDATE_HOURS):
                should_update = True
            
            if should_update:
                await message.answer("🔄 Updating data from osu!...")
                
                success = await api_client.update_user_in_db(session, user)
                
                if success:
                    stmt = select(User).where(User.telegram_id == tg_id)
                    result = await session.execute(stmt)
                    user = result.scalar_one_or_none()
                else:
                    await message.answer("⚠️ Failed to update data from osu! API.")

            profile_text = f"👤 **Profile:** `{message.from_user.full_name}`\n"
            profile_text += "═" * 35 + "\n\n"
            
            profile_text += f"🎮 **osu! nickname:** `{user.osu_username}`\n"
            profile_text += f"🆔 **osu! ID:** `{user.osu_user_id}`\n"
            
            if user.player_pp > 0:
                profile_text += f"📈 **PP:** `{user.player_pp:,}`\n"
                profile_text += f"🌍 **Rank:** `#{user.global_rank:,}`\n"
                
                country_emoji = get_country_emoji(user.country)
                profile_text += f"🏳️ **Country:** {country_emoji} `{user.country}`\n"
                
                profile_text += f"🎯 **Accuracy:** `{user.accuracy}%`\n"
                profile_text += f"🎮 **Play Count:** `{user.play_count:,}`\n"
            else:
                profile_text += "\n_Data from osu! has not been loaded yet_\n"
                profile_text += "Use `/refresh` to update\n"
            
            profile_text += "\n" + "═" * 35 + "\n\n"
            
            profile_text += f"🏆 **Hunter Points:** `{user.hps_points} HP`\n"
            profile_text += f"🎖️ **Rank:** `{user.rank}`\n"
            profile_text += f"📋 **Participation in bounties:** `{user.bounties_participated}`\n"
            
            if user.last_api_update:
                update_time = format_msk_time(user.last_api_update)
                profile_text += f"\n🕐 **Last updated:** `{update_time}`\n"
            
            # Подсказка
            profile_text += "\n💡 Use `/refresh` to update data"
            
            await message.answer(profile_text, parse_mode="Markdown")

        except Exception as e:
            print(f"Error in /profile for {tg_id}: {e}")
            await message.answer("❌ An error occurred while loading the profile.")
            raise


@router.message(Command("refresh"))
async def refresh_profile(message: types.Message, **kwargs):
    """
    Manually updating profile data from the osu! API.
    """
    tg_id = message.from_user.id
    
    api_client = kwargs.get("osu_api_client")
    if not api_client:
        await message.answer("❌ Error: API client not initialized.")
        return

    async for session in get_db_session():
        try:
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                await message.answer(
                    "❌ You are not registered.\n"
                    "Use `/register <nickname>`",
                    parse_mode="Markdown"
                )
                return

            # Обновляем
            await message.answer("🔄 Updating data from the osu! API...\n\n_This may take a few seconds._", parse_mode="Markdown")
            
            success = await api_client.update_user_in_db(session, user)
            
            if success:
                await message.answer(
                    f"✅ **Data updated!**\n\n"
                    f"📈 PP: `{user.player_pp:,}`\n"
                    f"🌍 Rank: `#{user.global_rank:,}`\n"
                    f"🎯 Accuracy: `{user.accuracy}%`",
                    parse_mode="Markdown"
                )
            else:
                await message.answer("❌ Failed to update data. Please try again later.")

        except Exception as e:
            print(f"Error in /refresh for {tg_id}: {e}")
            await message.answer("❌ An error occurred during the update.")
            raise


def get_country_emoji(country_code: str) -> str:
    """
    Returns the emoji flag of a country by code.
    """
    flags = {
        "RU": "🇷🇺",
        "US": "🇺🇸",
        "UA": "🇺🇦",
        "BY": "🇧🇾",
        "KZ": "🇰🇿",
        "PL": "🇵🇱",
        "DE": "🇩🇪",
        "FR": "🇫🇷",
        "GB": "🇬🇧",
        "JP": "🇯🇵",
        "KR": "🇰🇷",
        "CN": "🇨🇳",
        "CA": "🇨🇦",
        "AU": "🇦🇺",
        "BR": "🇧🇷",
        "XX": "🏳️",  # Unknown
    }
    
    return flags.get(country_code.upper(), "🏳️")

def format_msk_time(dt: datetime) -> str:
    """
    Converts UTC datetime to MSK and formats it for display.
    """
    if dt is None:
        return "Never"
    
    # Creating timezone
    utc = pytz.UTC
    msk = pytz.timezone('Europe/Moscow')
    
    # If the datetime does not have a timezone, we assume it is UTC.
    if dt.tzinfo is None:
        dt = utc.localize(dt)
    
    # Convert to MSK
    msk_time = dt.astimezone(msk)
    
    # Formating
    return msk_time.strftime("%d.%m.%Y %H:%M")


__all__ = ["router"]
