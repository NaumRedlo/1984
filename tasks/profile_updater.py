import asyncio
from sqlalchemy import select
from db.database import AsyncSessionFactory
from db.models.user import User
from services.refresh import refresh_user, needs_background_refresh
from utils.logger import get_logger

logger = get_logger("tasks.profile_updater")

class ProfileUpdater:
    CONCURRENT_WORKERS = 3

    def __init__(self, api_client):
        self.api_client = api_client
        self.semaphore = asyncio.Semaphore(self.CONCURRENT_WORKERS)

    async def _update_single_user_task(self, user_id: int):
        async with self.semaphore:
            async with AsyncSessionFactory() as session:
                try:
                    user = (await session.execute(
                        select(User).where(User.id == user_id)
                    )).scalar_one_or_none()
                    if not user:
                        return

                    ok = await refresh_user(user, session, self.api_client, mode="background_full")
                    if ok:
                        await session.commit()
                        logger.info(f"Background update success: {user.osu_username}")
                    else:
                        logger.warning(f"Background update failed or skipped: user_id={user_id}")
                except Exception as e:
                    logger.error(f"Error in background task for user_id {user_id}: {e}")

    async def get_stale_user_ids(self) -> list[int]:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(User.id, User.last_api_update))
            return [
                row[0] for row in result.fetchall()
                if needs_background_refresh(row[1])
            ]

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
