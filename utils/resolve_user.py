from typing import Optional, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.user import User


async def resolve_osu_user(api_client, query: str) -> Optional[Dict[str, Any]]:
    """Parse 'id:' prefix and fetch user from osu! API."""
    query = query.strip()
    if query.lower().startswith("id:"):
        osu_id = int(query[3:].strip())
        return await api_client.get_user_data(osu_id)
    return await api_client.get_user_data(query)


async def get_registered_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Fetch registered user by telegram_id."""
    stmt = select(User).where(User.telegram_id == telegram_id)
    return (await session.execute(stmt)).scalar_one_or_none()
