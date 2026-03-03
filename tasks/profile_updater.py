import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from db.database import AsyncSessionFactory
from db.models.user import User
from utils.logger import get_logger

logger = get_logger("tasks.profile_updater")

class ProfileUpdater:
    CONCURRENT_WORKERS = 3
    UPDATE_THRESHOLD_HOURS = 6
    API_COOLDOWN = 1.0

    def __init__(self, api_client):
        self.api_client = api_client
        self.semaphore = asyncio.Semaphore(self.CONCURRENT_WORKERS)

    async def _update_single_user_task(self, user_id: int):
        async with self.semaphore:
            async with AsyncSessionFactory() as session:
                try:
                    stmt = select(User).where(User.id == user_id)
                    result = await session.execute(stmt)
                    user = result.scalar_one_or_none()

                    if not user:
                        return

                    success = await self.api_client.update_user_in_db(session, user)
                    
                    if success:
                        logger.info(f"Background update success: {user.osu_username}")
                    else:
                        logger.warning(f"Background update failed: {user.osu_username}")
                        
                except Exception as e:
                    logger.error(f"Error in background task for user_id {user_id}: {e}")
                
                await asyncio.sleep(self.API_COOLDOWN)

    async def get_stale_user_ids(self) -> list[int]:
        threshold = datetime.now(timezone.utc) - timedelta(hours=self.UPDATE_THRESHOLD_HOURS)
        async with AsyncSessionFactory() as session:
            stmt = (
                select(User.id)
                .where((User.last_api_update < threshold) | (User.last_api_update.is_(None)))
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.fetchall()]

    async def start_loop(self, shutdown_event: asyncio.Event):
        logger.info("ProfileUpdater engine started.")
        
        while not shutdown_event.is_set():
            try:
                stale_ids = await self.get_stale_user_ids()
                
                if stale_ids:
                    logger.info(f"Found {len(stale_ids)} stale profiles. Starting update...")
                    tasks = [self._update_single_user_task(uid) for uid in stale_ids]
                    await asyncio.gather(*tasks)
                    logger.info("Batch update finished.")
                
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    continue

            except Exception as e:
                logger.critical(f"Critical error in ProfileUpdater loop: {e}", exc_info=True)
                await asyncio.sleep(60)

async def periodic_profile_updates(api_client, shutdown_event: asyncio.Event):
    updater = ProfileUpdater(api_client)
    await updater.start_loop(shutdown_event)
