import asyncio
from typing import Optional

from utils.logger import get_logger
from utils.timeutils import utcnow
from services.refresh.policy import RefreshMode

logger = get_logger("services.refresh.orchestrator")

_in_flight: set[tuple] = set()
_in_flight_lock = asyncio.Lock()
_players: dict[int, asyncio.Lock] = {}

def _one_at_a_time(user) -> asyncio.Lock:
    player_id = getattr(user, "player_id", None)
    if player_id is None:
        return asyncio.Lock()
    return _players.setdefault(player_id, asyncio.Lock())

def _key(user) -> tuple:
    return (type(user).__name__, user.id)

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
    key = _key(user)
    if not await _acquire(key):
        logger.debug(f"Skipping refresh for {key}: already in-flight")
        return False

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
            async with _one_at_a_time(user):
                await api_client.sync_user_best_scores(user, session, oauth_token=oauth_token)

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

    finally:
        await _release(key)

def is_in_flight(user_id: int) -> bool:
    return ("User", user_id) in _in_flight
