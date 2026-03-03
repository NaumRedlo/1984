# bot/handlers/hps_handlers.py
"""
HPS 2.0 Calculator Handler
Command to calculate potential HP for a map.
"""

import re
from typing import Optional
from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select
from db.models.user import User
from db.database import get_db_session
from utils.hp_calculator import calculate_hps

router = Router()


def extract_beatmap_id(text: str) -> Optional[str]:
    """
    Extract beatmap ID from link or text.
    Supports formats:
    - https://osu.ppy.sh/beatmaps/123456
    - https://osu.ppy.sh/beatmapsets/123456#osu/789012
    - 123456 (just number)
    """
    patterns = [
        r'osu\.ppy\.sh/beatmaps/(\d+)',
        r'osu\.ppy\.sh/beatmapsets/\d+.*?/(\d+)',
        r'^(\d+)$',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    
    return None


async def get_community_stats(session) -> dict:
    """
    Get community statistics for Relativity Factor calculation.
    """
    stmt = select(User.player_pp).where(User.player_pp.isnot(None))
    result = await session.execute(stmt)
    all_pp = [row[0] for row in result.fetchall() if row[0] is not None and row[0] > 0]
    
    if len(all_pp) < 3:
        return {
            "p25": 2000,
            "p40": 4500,
            "p60": 7000,
            "p75": 10000,
        }
    
    all_pp.sort()
    count = len(all_pp)
    
    def get_percentile(p):
        idx = int(count * p / 100)
        return all_pp[min(idx, count - 1)]
    
    return {
        "p25": get_percentile(25),
        "p40": get_percentile(40),
        "p60": get_percentile(60),
        "p75": get_percentile(75),
    }


@router.message(Command("hps"))
async def calculate_hps_command(message: types.Message, **kwargs):
    """
    /hps command - Calculate potential HP for a map.
    
    Usage:
    /hps last - Analyze last played map
    /hps <beatmap_id> - Analyze specific map by ID
    /hps <link> - Analyze by map link
    """
    tg_id = message.from_user.id
    api_client = kwargs.get("osu_api_client")
    
    if not api_client:
        await message.answer("❌ Error: API client not initialized.")
        return
    
    args = message.text.split()[1:] if message.text else []
    
    async for session in get_db_session():
        try:
            # Find user
            stmt = select(User).where(User.telegram_id == tg_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                await message.answer(
                    "❌ You are not registered.\n"
                    "Use `/register <osu_nickname>`",
                    parse_mode="Markdown"
                )
                return
            
            # Get map data
            if not args or args[0] == "last":
                # Analyze last played map
                await message.answer("🔄 Fetching last played map...")
                
                scores = await api_client.get_user_recent_scores(user.osu_user_id, limit=1)
                
                if not scores:
                    await message.answer("❌ Failed to retrieve recent scores.")
                    return
                
                score = scores[0]
                beatmap = score.get("beatmap", {})
                beatmapset = score.get("beatmapset", {})
                
                star_rating = beatmap.get("difficulty_rating", 5.0)
                # ← CHANGE: Use total_length instead of drain_time
                map_duration = beatmap.get("total_length", None)
                
                if map_duration is None:
                    map_duration = 180
                elif float(map_duration) < 30:
                    print(f"⚠️ Suspicious map duration: {map_duration}. Using 180.")
                    map_duration = 180
                
                accuracy = score.get("accuracy", 0.0) * 100
                is_fc = score.get("max_combo", 0) >= beatmap.get("max_combo", 0)
                
                map_title = f"{beatmapset.get('artist', 'Unknown')} - {beatmapset.get('title', 'Unknown')}"
                map_diff = beatmap.get("version", "Unknown")
            
            else:
                # Analyze by ID or link
                beatmap_input = args[0]
                beatmap_id = extract_beatmap_id(beatmap_input)
                
                if not beatmap_id:
                    await message.answer(
                        "❌ Could not extract map ID!\n\n"
                        "📋 **Example formats:**\n"
                        "`/hps 1234567`\n"
                        "`/hps https://osu.ppy.sh/beatmaps/1234567`\n"
                        "`/hps last` — last played map",
                        parse_mode="Markdown"
                    )
                    return
                
                await message.answer(f"🔄 Getting map data for {beatmap_id}...")
                
                beatmap = await api_client.get_beatmap(beatmap_id)
                
                if not beatmap:
                    await message.answer(
                        f"❌ Map not found!\n\n"
                        f"Check that you're using **beatmap** ID (difficulty), not **beatmapset** (song).\n"
                        f"ID: `{beatmap_id}`",
                        parse_mode="Markdown"
                    )
                    return
                
                star_rating = beatmap.get("difficulty_rating", 5.0)
                # ← CHANGE: Use total_length instead of drain_time
                map_duration = beatmap.get("total_length", None)
                
                if map_duration is None:
                    map_duration = 180
                elif float(map_duration) < 30:
                    print(f"⚠️ Suspicious map duration: {map_duration}. Using 180.")
                    map_duration = 180
                
                map_title = f"{beatmap.get('beatmapset', {}).get('artist', 'Unknown')} - {beatmap.get('beatmapset', {}).get('title', 'Unknown')}"
                map_diff = beatmap.get("version", "Unknown")
                accuracy = 95.0
                is_fc = False
            
            # Convert to seconds for calculation
            duration_seconds = int(float(map_duration))
            
            # Get community stats
            community_stats = await get_community_stats(session)
            
            # Calculate HP scenarios
            scenarios = [
                {"type": "win", "name": "🥇 Victory", "hp": 100},
                {"type": "condition", "name": "✅ Condition (FC/SS)", "hp": 60},
                {"type": "partial", "name": "⚠️ Partially", "hp": 30},
                {"type": "participation", "name": "📋 Participation", "hp": 10},
            ]
            
            response = f"""
🧮 **HPS 2.0 — MAP ANALYSIS**
{"═" * 35}

🎵 **Map:** {map_title} [{map_diff}]
⭐ **Difficulty:** {star_rating:.2f}★
⏱️ **Duration:** {duration_seconds // 60}:{duration_seconds % 60:02d}

{"═" * 35}
📊 **POTENTIAL HP:**
"""
            
            for scenario in scenarios:
                result = calculate_hps(
                    result_type=scenario["type"],
                    star_rating=star_rating,
                    drain_time_seconds=duration_seconds,
                    player_pp=user.player_pp or 0,
                    community_stats=community_stats,
                    accuracy=accuracy if scenario["type"] == "win" else 0.0,
                    is_full_combo=is_fc if scenario["type"] in ["win", "condition"] else False,
                )
                
                response += f"\n{scenario['name']}: **{result['final_hp']} HP**"
                response += f" _×{result['total_multiplier']}_ "
            
            rf_value = calculate_hps(
                result_type="win",
                star_rating=star_rating,
                drain_time_seconds=duration_seconds,
                player_pp=user.player_pp or 0,
                community_stats=community_stats,
            )["relativity_factor"]["value"]
            
            response += f"""

{"═" * 35}
🎯 **Your PP:** {user.player_pp or 0}
📈 **Progress Multiplier:** x{rf_value}

💡 **Tip:** Use `/submit` to submit results!
"""
            
            await message.answer(response, parse_mode="Markdown")
            
        except Exception as e:
            print(f"Error in /hps for {tg_id}: {e}")
            await message.answer("❌ An error occurred during calculation.")
            raise


__all__ = ["router"]
