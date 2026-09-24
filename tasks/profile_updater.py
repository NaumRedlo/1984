import asyncio
import time
from collections import Counter
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
                        return "gone"

                    ok = await refresh_user(user, session, self.api_client, mode="background_full")
                    if ok:
                        await session.commit()
                        logger.debug(f"Background update success: {user.osu_username}")
                        return "updated"
                    logger.warning(f"Background update failed or skipped: user_id={user_id}")
                    return "failed"
                except Exception as e:
                    logger.error(f"Error in background task for user_id {user_id}: {e}")
                    return "failed"

    async def get_stale_user_ids(self) -> list[int]:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(User.id, User.last_full_update))
            return [
                row[0] for row in result.fetchall()
                if needs_background_refresh(row[1])
            ]

    async def get_stats_sweep_ids(self) -> list[int]:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(User.id, User.last_api_update)
                .where(User.osu_user_id.isnot(None))
                .order_by(User.last_api_update.asc().nullsfirst())
            )
            due = [row[0] for row in result.fetchall() if needs_stats_sweep(row[1])]
        return due[:self.SWEEP_BATCH_LIMIT]

    async def _sweep_single_user_task(self, user_id: int):
        async with self.semaphore:
            async with AsyncSessionFactory() as session:
                try:
                    user = (await session.execute(
                        select(User).where(User.id == user_id)
                    )).scalar_one_or_none()
                    if not user:
                        return "gone"
                    if await refresh_user(user, session, self.api_client, mode="stats_only"):
                        await session.commit()
                        return "updated"
                    return "failed"
                except Exception as e:
                    logger.debug(f"Stats sweep failed for user_id={user_id}: {e}")
                    return "failed"

    async def start_loop(self, shutdown_event: asyncio.Event):
        logger.info("ProfileUpdater engine started.")

        while not shutdown_event.is_set():
            try:

                try:
                    async with AsyncSessionFactory() as session:
                        await ensure_period_snapshot(session)
                except Exception as e:
                    logger.warning(f"Leaderboard snapshot capture failed: {e}", exc_info=True)

                sweep_ids = await self.get_stats_sweep_ids()
                if sweep_ids:
                    started = time.monotonic()
                    results = await asyncio.gather(
                        *(self._sweep_single_user_task(uid) for uid in sweep_ids),
                        return_exceptions=True,
                    )
                    _log_round("Stats sweep", results, started)

                stale_ids = await self.get_stale_user_ids()

                if stale_ids:
                    started = time.monotonic()
                    tasks = [self._update_single_user_task(uid) for uid in stale_ids]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    _log_round("Background update", results, started)

                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=self.TICK_SECONDS)
                except asyncio.TimeoutError:
                    continue

            except Exception as e:
                logger.critical(f"Critical error in ProfileUpdater loop: {e}", exc_info=True)
                await asyncio.sleep(60)

def _log_round(what: str, results: list, started: float) -> None:
    """One line per round: how many profiles were refreshed and how many osu! did not give."""
    counts = Counter("crashed" if isinstance(r, BaseException) else (r or "failed") for r in results)
    took = time.monotonic() - started
    line = (f"{what}: {counts['updated']}/{len(results)} updated, {counts['failed']} failed, "
            f"{counts['crashed']} crashed, {counts['gone']} gone in {took:.0f}s")
    bad = counts["failed"] + counts["crashed"]
    if bad and bad * 2 >= len(results):
        logger.error(line + " — is osu! answering?")
    elif bad:
        logger.warning(line)
    else:
        logger.info(line)
    if counts["crashed"]:
        first = next(r for r in results if isinstance(r, BaseException))
        logger.error(f"{what}: first crash: {first!r}")

async def periodic_profile_updates(api_client, shutdown_event: asyncio.Event):
    updater = ProfileUpdater(api_client)
    await updater.start_loop(shutdown_event)
