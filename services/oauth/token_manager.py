"""
Token lifecycle: get a valid token (refreshing if needed), revoke tokens.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from sqlalchemy import select, delete

from config.settings import OSU_CLIENT_ID, OSU_CLIENT_SECRET
from db.database import get_db_session
from db.models.oauth_token import OAuthToken
from utils.crypto import encrypt_token, decrypt_token
from utils.logger import get_logger

logger = get_logger("oauth.token_manager")

TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


async def get_valid_token(user_id: int) -> Optional[str]:
    """Return a valid access token for user_id, refreshing if expired. None if no token."""
    async with get_db_session() as session:
        stmt = select(OAuthToken).where(OAuthToken.user_id == user_id)
        token_row = (await session.execute(stmt)).scalar_one_or_none()
        if not token_row:
            return None

        now = datetime.now(timezone.utc)
        expiry = token_row.token_expiry
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry and now < expiry - TOKEN_REFRESH_BUFFER:
            return decrypt_token(token_row.access_token_enc)

        if not token_row.refresh_token_enc:
            logger.warning(f"Token expired and no refresh token for user_id={user_id}")
            return None

        refresh_token = decrypt_token(token_row.refresh_token_enc)
        new_tokens = await _refresh_access_token(refresh_token)
        if not new_tokens:
            logger.error(f"Token refresh failed for user_id={user_id}")
            return None

        token_row.access_token_enc = encrypt_token(new_tokens["access_token"])
        if new_tokens.get("refresh_token"):
            token_row.refresh_token_enc = encrypt_token(new_tokens["refresh_token"])
        token_row.token_expiry = now + timedelta(seconds=new_tokens.get("expires_in", 86400))
        token_row.updated_at = now
        await session.commit()

        logger.info(f"Token refreshed for user_id={user_id}")
        return new_tokens["access_token"]


async def has_oauth(user_id: int) -> bool:
    """Check if user has a stored OAuth token."""
    async with get_db_session() as session:
        stmt = select(OAuthToken.id).where(OAuthToken.user_id == user_id)
        result = (await session.execute(stmt)).scalar_one_or_none()
        return result is not None


async def revoke_token(user_id: int) -> None:
    """Delete stored OAuth token for a user."""
    async with get_db_session() as session:
        await session.execute(
            delete(OAuthToken).where(OAuthToken.user_id == user_id)
        )
        await session.commit()
    logger.info(f"OAuth token revoked for user_id={user_id}")


async def _refresh_access_token(refresh_token: str) -> Optional[dict]:
    async with aiohttp.ClientSession() as session:
        data = {
            "client_id": OSU_CLIENT_ID,
            "client_secret": OSU_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        async with session.post("https://osu.ppy.sh/oauth/token", data=data) as resp:
            if resp.status != 200:
                error = await resp.text()
                logger.error(f"Token refresh failed: {resp.status} {error[:200]}")
                return None
            return await resp.json()
