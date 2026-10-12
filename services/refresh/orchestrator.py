import asyncio
from typing import Optional
from contextlib import asynccontextmanager

from utils.logger import get_logger
from utils.timeutils import utcnow
from services.refresh.policy import RefreshMode

logger = get_logger("services.refresh.orchestrator")

_in_flight: set[tuple] = set()
_in_flight_lock = asyncio.Lock()
_in_flight_users: set[int] = set()

def _key(user) -> tuple:
    osu_id = getattr(user, "osu_user_id", None)
    return ("osu", osu_id) if osu_id else (type(user).__name__, user.id)

async def _acquire(key: tuple) -> bool:
    async with _in_flight_lock:
        if key in _in_flight:
            return False
        _in_flight.add(key)
        return True

async def _release(key: tuple) -> None:
    async with _in_flight_lock:
        _in_flight.discard(key)

async def refresh_user(
    user,
    session,
    api_client,
    mode: RefreshMode = "full",
    oauth_token: Optional[str] = None,
) -> bool:
    async with claim_refresh(user) as acquired:
        if not acquired:
            return False
        return await apply_refresh(user, session, api_client, mode, oauth_token)


@asynccontextmanager
async def claim_refresh(user):
    key = _key(user)
    acquired = await _acquire(key)
    user_id = user.id if type(user).__name__ == "User" else None
    if acquired and user_id is not None:
        _in_flight_users.add(user_id)
    try:
        yield acquired
    finally:
        if acquired:
            if user_id is not None:
                _in_flight_users.discard(user_id)
            await _release(key)


async def apply_refresh(user, session, api_client, mode="full", oauth_token=None):
    try:
        if oauth_token is None:
            from services.oauth.token_manager import token_of
            try:
                oauth_token = await token_of(user)
            except Exception:
                oauth_token = None

        ok = await api_client.sync_user_stats_from_api(user, oauth_token=oauth_token)
        if not ok:
            logger.warning(f"sync_user_stats_from_api failed for user_id={user.id}")
            return False

        if mode in ("full", "background_full"):
            if not await api_client.sync_user_best_scores(user, session, oauth_token=oauth_token):
                return False

            try:
                from utils.title_progress import refresh_user_titles
                await refresh_user_titles(user, session)
            except Exception as exc:
                logger.warning(f"title refresh failed for user_id={user.id}: {exc}")

            user.last_full_update = utcnow()
            await session.flush()
        logger.debug(f"Refresh done ({mode}) for {user.osu_username} (id={user.id})")
        return True

    except Exception as exc:
        logger.error(f"Refresh error for user_id={user.id}: {exc}", exc_info=True)
        return False


def is_in_flight(user_id: int) -> bool:
    return user_id in _in_flight_users
