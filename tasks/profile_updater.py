import asyncio
from sqlalchemy import select
from db.database import AsyncSessionFactory
from db.models.user import User
from services.refresh import refresh_user, needs_background_refresh, needs_stats_sweep
from services.leaderboard.snapshots import ensure_period_snapshot
from utils.logger import get_logger

logger = get_logger("tasks.profile_updater")

class ProfileUpdater:
    CONCURRENT_WORKERS = 3
    TICK_SECONDS = 300
    # Ceiling on one tick's stats sweep. At 0.2s per API call (the client's own
    # rate limit) 150 users take ~30s — comfortably inside a tick, and larger
    # groups simply roll over into the next one (oldest-first ordering).
    SWEEP_BATCH_LIMIT = 150

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
        """Users due the expensive pass (best scores + titles)."""
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(User.id, User.last_full_update))
            return [
                row[0] for row in result.fetchall()
                if needs_background_refresh(row[1])
            ]

    async def get_stats_sweep_ids(self) -> list[int]:
        """Users whose headline stats are stale enough to re-pull.

        Oldest first and capped: the sweep runs every tick, so on a big group
        this spreads the API load over several ticks instead of spiking.
        """
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(User.id, User.last_api_update)
                .where(User.osu_user_id.isnot(None))
                .order_by(User.last_api_update.asc().nullsfirst())
            )
            due = [row[0] for row in result.fetchall() if needs_stats_sweep(row[1])]
        return due[:self.SWEEP_BATCH_LIMIT]

    async def _sweep_single_user_task(self, user_id: int):
        """One cheap stats-only refresh — a single osu! API call."""
        async with self.semaphore:
            async with AsyncSessionFactory() as session:
                try:
                    user = (await session.execute(
                        select(User).where(User.id == user_id)
                    )).scalar_one_or_none()
                    if not user:
                        return
                    if await refresh_user(user, session, self.api_client, mode="stats_only"):
                        await session.commit()
                except Exception as e:
                    logger.debug(f"Stats sweep failed for user_id={user_id}: {e}")

    async def start_loop(self, shutdown_event: asyncio.Event):
        logger.info("ProfileUpdater engine started.")

        while not shutdown_event.is_set():
            try:
                # Open a new leaderboard period if the week rolled over. Cheap
                # no-op once the current period's anchors exist, so it can ride
                # along on every tick instead of needing its own scheduler.
                try:
                    async with AsyncSessionFactory() as session:
                        await ensure_period_snapshot(session)
                except Exception as e:
                    logger.warning(f"Leaderboard snapshot capture failed: {e}", exc_info=True)

                # Fast pass: keep the leaderboard's numbers live. One API call
                # per user (sync_user_stats_from_api covers every ranked
                # metric), so this is cheap enough to run on every tick.
                sweep_ids = await self.get_stats_sweep_ids()
                if sweep_ids:
                    logger.info(f"Stats sweep: refreshing {len(sweep_ids)} profiles...")
                    await asyncio.gather(
                        *(self._sweep_single_user_task(uid) for uid in sweep_ids),
                        return_exceptions=True,
                    )

                stale_ids = await self.get_stale_user_ids()

                if stale_ids:
                    logger.info(f"Found {len(stale_ids)} stale profiles. Starting update...")
                    tasks = [self._update_single_user_task(uid) for uid in stale_ids]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    failures = [r for r in results if isinstance(r, Exception)]
                    if failures:
                        logger.error(
                            f"Batch update finished with {len(failures)}/{len(results)} "
                            f"unexpected errors; first: {failures[0]!r}"
                        )
                    else:
                        logger.info("Batch update finished.")

                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=self.TICK_SECONDS)
                except asyncio.TimeoutError:
                    continue

            except Exception as e:
                logger.critical(f"Critical error in ProfileUpdater loop: {e}", exc_info=True)
                await asyncio.sleep(60)

async def periodic_profile_updates(api_client, shutdown_event: asyncio.Event):
    updater = ProfileUpdater(api_client)
    await updater.start_loop(shutdown_event)
