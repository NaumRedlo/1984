"""
Shared refresh orchestrator.

Single entry point for all profile refresh paths:
  - /profile auto-refresh
  - /refresh command
  - background ProfileUpdater
  - leaderboard stale refresh

Provides per-user in-flight deduplication so parallel callers
(e.g. background updater + /profile) never double-refresh the same user.
"""

import asyncio
from typing import Optional

from utils.logger import get_logger
from services.refresh.policy import RefreshMode

logger = get_logger("services.refresh.orchestrator")

# In-memory set of user DB IDs currently being refreshed
_in_flight: set[int] = set()
_in_flight_lock = asyncio.Lock()


async def _acquire(user_id: int) -> bool:
    """Try to claim a refresh slot for user_id. Returns False if already in-flight."""
    async with _in_flight_lock:
        if user_id in _in_flight:
            return False
        _in_flight.add(user_id)
        return True


async def _release(user_id: int) -> None:
    async with _in_flight_lock:
        _in_flight.discard(user_id)


async def refresh_user(
    user,
    session,
    api_client,
    mode: RefreshMode = "full",
    oauth_token: Optional[str] = None,
) -> bool:
    if not await _acquire(user.id):
        logger.debug(f"Skipping refresh for user_id={user.id}: already in-flight")
        return False

    try:
        if oauth_token is None:
            from services.oauth.token_manager import get_valid_token
            try:
                oauth_token = await get_valid_token(user.id)
            except Exception:
                oauth_token = None

        ok = await api_client.sync_user_stats_from_api(user, oauth_token=oauth_token)
        if not ok:
            logger.warning(f"sync_user_stats_from_api failed for user_id={user.id}")
            return False

        if mode in ("full", "background_full"):
            await api_client.sync_user_best_scores(user, session, oauth_token=oauth_token)

        logger.debug(f"Refresh done ({mode}) for {user.osu_username} (id={user.id})")
        return True

    except Exception as exc:
        logger.error(f"Refresh error for user_id={user.id}: {exc}", exc_info=True)
        return False

    finally:
        await _release(user.id)


def is_in_flight(user_id: int) -> bool:
    """Non-blocking check — True if a refresh is already running for this user."""
    return user_id in _in_flight
