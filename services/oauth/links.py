import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update

from config.settings import OSU_CLIENT_ID, OSU_OAUTH_REDIRECT_URI, OSU_OAUTH_SCOPES
from db.database import get_db_session
from db.models.oauth_pending import OAuthPending

STATE_TTL = timedelta(minutes=15)

async def sweep_expired_states(session, now: datetime) -> None:
    await session.execute(delete(OAuthPending).where(OAuthPending.issued_at < now - STATE_TTL))

async def generate_oauth_url(telegram_id: int) -> str:
    now = datetime.now(timezone.utc)
    state = secrets.token_urlsafe(32)
    async with get_db_session() as session:
        await sweep_expired_states(session, now)
        session.add(OAuthPending(state=state, telegram_id=telegram_id, issued_at=now))
        await session.commit()
    return authorize_url(state)

def authorize_url(state: str) -> str:
    return (
        f"https://osu.ppy.sh/oauth/authorize"
        f"?client_id={OSU_CLIENT_ID}"
        f"&redirect_uri={OSU_OAUTH_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={OSU_OAUTH_SCOPES.replace(' ', '+')}"
        f"&state={state}"
    )

async def track_link_message(telegram_id: int, chat_id: int, message_id: int) -> None:
    async with get_db_session() as session:
        await session.execute(
            update(OAuthPending)
            .where(OAuthPending.telegram_id == telegram_id, OAuthPending.message_id.is_(None))
            .values(chat_id=chat_id, message_id=message_id)
        )
        await session.commit()
