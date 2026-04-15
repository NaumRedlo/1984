from typing import Optional, Dict, Any, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.user import User


class OsuUserLookupError(Exception):
    pass


class OsuUserNotFoundError(OsuUserLookupError):
    pass


async def resolve_osu_user(api_client, query: str) -> Optional[Dict[str, Any]]:
    """Resolve an osu! user query via the API client."""
    query = query.strip().lstrip("@").strip()
    if not query:
        return None

    lowered = query.lower()
    if lowered.startswith("id:"):
        try:
            osu_id = int(query[3:].strip())
        except ValueError:
            return None
        return await api_client.get_user_data(osu_id)

    return await api_client.get_user_data(query)


async def get_registered_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Fetch linked user by telegram_id."""
    stmt = select(User).where(User.telegram_id == telegram_id, User.osu_user_id.isnot(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_any_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Fetch any user row by telegram_id."""
    stmt = select(User).where(User.telegram_id == telegram_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_registered_user_by_osu(
    session: AsyncSession,
    osu_user_id: Optional[int] = None,
    osu_username: Optional[str] = None,
) -> Optional[User]:
    """Fetch registered user by osu! account identity."""
    if osu_user_id is not None:
        stmt = select(User).where(User.osu_user_id == osu_user_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user:
            return user

    if osu_username:
        stmt = select(User).where(func.lower(User.osu_username) == osu_username.lower())
        return (await session.execute(stmt)).scalar_one_or_none()

    return None


async def resolve_registered_user(
    session: AsyncSession,
    api_client,
    query: str,
) -> Tuple[Optional[User], Optional[Dict[str, Any]]]:
    """Resolve an osu! query and match it to a registered user if possible."""
    user_data = await resolve_osu_user(api_client, query)
    if not user_data:
        return None, None

    user = await get_registered_user_by_osu(
        session,
        osu_user_id=user_data.get("id"),
        osu_username=user_data.get("username"),
    )
    return user, user_data


async def resolve_osu_query_status(
    session: AsyncSession,
    api_client,
    query: str,
) -> Tuple[Optional[User], Optional[Dict[str, Any]], str]:
    """Resolve an osu! query and report whether it was found and registered.

    Returns (registered_user, user_data, status) where status is one of:
    - "registered": found in osu! and locally registered
    - "unregistered": found in osu! but not registered locally
    - "not_found": not found in osu!
    """
    user_data = await resolve_osu_user(api_client, query)
    if not user_data:
        return None, None, "not_found"

    user = await get_registered_user_by_osu(
        session,
        osu_user_id=user_data.get("id"),
        osu_username=user_data.get("username"),
    )
    if user:
        return user, user_data, "registered"
    return None, user_data, "unregistered"
