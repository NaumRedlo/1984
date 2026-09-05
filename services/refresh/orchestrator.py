import asyncio
from typing import Optional

from utils.logger import get_logger
from utils.timeutils import utcnow
from services.refresh.policy import RefreshMode

logger = get_logger("services.refresh.orchestrator")

_in_flight: set[int] = set()
_in_flight_lock = asyncio.Lock()

async def _acquire(user_id: int) -> bool:
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

                oauth_token = await get_valid_token(user.telegram_id)
            except Exception:
                oauth_token = None

        ok = await api_client.sync_user_stats_from_api(user, oauth_token=oauth_token)
        if not ok:
            logger.warning(f"sync_user_stats_from_api failed for user_id={user.id}")
            return False

        if mode in ("full", "background_full"):
            await api_client.sync_user_best_scores(user, session, oauth_token=oauth_token)

            try:
                from utils.title_progress import refresh_user_titles
                await refresh_user_titles(user, session)
            except Exception as exc:
                logger.warning(f"title refresh failed for user_id={user.id}: {exc}")

            user.last_full_update = utcnow()
        logger.debug(f"Refresh done ({mode}) for {user.osu_username} (id={user.id})")
        return True

    except Exception as exc:
        logger.error(f"Refresh error for user_id={user.id}: {exc}", exc_info=True)
        return False

    finally:
        await _release(user.id)

def is_in_flight(user_id: int) -> bool:
    return user_id in _in_flight
