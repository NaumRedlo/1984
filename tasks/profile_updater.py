"""
Background task for automatically updating player profiles.
Runs every 30 minutes and updates PP, rank, and accuracy from the osu! API.
"""

import asyncio
from datetime import datetime, timezone
import pytz
from sqlalchemy import select
from db.models.user import User


async def periodic_profile_updates(api_client, session_factory):
    """
    Background task: updates all player profiles every 30 minutes.
    
    Args:
        api_client: Copy OsuApiClient
        session_factory: DB session factory
    """
    print("🔄 Profile updater task started (30min interval)")
    
    while True:
        try:
            async for session in session_factory():
                stmt = select(User)
                result = await session.execute(stmt)
                users = result.scalars().all()
                
                print(f"📊 Updating {len(users)} users from osu! API...")
                
                updated_count = 0
                for i, user in enumerate(users, 1):
                    try:
                        if not user.osu_user_id:
                            print(f"  ⚠️ Skipping {user.osu_username} (no osu! ID)")
                            continue
                        
                        stats = await api_client.get_user_stats(user.osu_user_id)
                        
                        if stats:
                            user.player_pp = int(stats.get("pp", 0))
                            user.global_rank = stats.get("global_rank", 0)
                            user.country = stats.get("country_code", "XX")
                            user.accuracy = round(stats.get("accuracy", 0.0), 2)
                            user.play_count = stats.get("play_count", 0)
                            user.last_api_update = datetime.now(timezone.utc)
                            
                            updated_count += 1
                            
                            if i < len(users):
                                await asyncio.sleep(1)
                        else:
                            print(f"  ⚠️ Failed to get stats for {user.osu_username}")
                            
                    except Exception as e:
                        print(f"  ❌ Error updating {user.osu_username}: {e}")
                        continue
                
                await session.commit()
                print(f"✅ Updated {updated_count}/{len(users)} users successfully")
            
            print("⏳ Waiting 30 minutes before next update...")
            await asyncio.sleep(1800)
            
        except Exception as e:
            print(f"❌ Error in periodic updater: {e}")
            print("⏳ Waiting 5 minutes before retry...")
            await asyncio.sleep(300)
