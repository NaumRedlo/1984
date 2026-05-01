"""
Token lifecycle: get a valid token (refreshing if needed), revoke tokens.
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from cryptography.fernet import InvalidToken
from sqlalchemy import select, delete

from config.settings import OSU_CLIENT_ID, OSU_CLIENT_SECRET
from db.database import get_db_session
from db.models.oauth_token import OAuthToken
from utils.crypto import encrypt_token, decrypt_token
from utils.logger import get_logger

logger = get_logger("oauth.token_manager")

TOKEN_REFRESH_BUFFER = timedelta(minutes=10)
REFRESH_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)
PERMANENT_OAUTH_ERRORS = frozenset({
    "invalid_grant",
    "invalid_client",
    "unauthorized_client",
    "unsupported_grant_type",
})

# Per-user lock to serialize refresh attempts and avoid racing the refresh token.
_refresh_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def get_valid_token(user_id: int) -> Optional[str]:
    """Return a valid access token for user_id, refreshing if expired. None if no token."""
    async with _refresh_locks[user_id]:
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
                try:
                    return decrypt_token(token_row.access_token_enc)
                except InvalidToken:
                    logger.error(
                        f"Cannot decrypt access token for user_id={user_id} "
                        f"(OAUTH_ENCRYPTION_KEY changed?). Deleting row."
                    )
                    await session.delete(token_row)
                    await session.commit()
                    return None

            if not token_row.refresh_token_enc:
                logger.warning(f"Token expired and no refresh token for user_id={user_id}")
                await session.delete(token_row)
                await session.commit()
                return None

            try:
                refresh_token = decrypt_token(token_row.refresh_token_enc)
            except InvalidToken:
                logger.error(
                    f"Cannot decrypt refresh token for user_id={user_id} "
                    f"(OAUTH_ENCRYPTION_KEY changed?). Deleting row."
                )
                await session.delete(token_row)
                await session.commit()
                return None

            new_tokens, permanent = await _refresh_access_token(refresh_token)
            if not new_tokens:
                if permanent:
                    logger.error(
                        f"Refresh token rejected for user_id={user_id} — deleting row, "
                        f"user must re-link."
                    )
                    await session.delete(token_row)
                    await session.commit()
                else:
                    logger.warning(f"Transient token refresh failure for user_id={user_id}")
                return None

            new_access = new_tokens.get("access_token")
            if not new_access:
                logger.error(f"Refresh response missing access_token for user_id={user_id}")
                return None

            new_refresh = new_tokens.get("refresh_token")
            if not new_refresh:
                # osu! always rotates — missing refresh_token is unexpected.
                logger.warning(
                    f"Refresh response missing refresh_token for user_id={user_id}; "
                    f"keeping previous (next refresh may fail)."
                )

            token_row.access_token_enc = encrypt_token(new_access)
            if new_refresh:
                token_row.refresh_token_enc = encrypt_token(new_refresh)
            token_row.token_expiry = now + timedelta(seconds=new_tokens.get("expires_in", 86400))
            token_row.updated_at = now
            await session.commit()

            logger.info(f"Token refreshed for user_id={user_id}")
            return new_access


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


async def _refresh_access_token(refresh_token: str) -> tuple[Optional[dict], bool]:
    """
    Returns (tokens_dict_or_None, is_permanent_failure).
    permanent=True means the refresh token will never work again (revoked, rotated out).
    """
    try:
        async with aiohttp.ClientSession(timeout=REFRESH_HTTP_TIMEOUT) as session:
            data = {
                "client_id": OSU_CLIENT_ID,
                "client_secret": OSU_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
            async with session.post("https://osu.ppy.sh/oauth/token", data=data) as resp:
                body = await resp.text()
                if resp.status == 200:
                    try:
                        return await resp.json(content_type=None), False
                    except Exception as e:
                        logger.error(f"Refresh response not JSON: {e} body={body[:200]}")
                        return None, False

                # Try to parse OAuth error code from body.
                err_code = ""
                try:
                    import json as _json
                    err_code = (_json.loads(body) or {}).get("error", "") or ""
                except Exception:
                    pass

                permanent = (
                    resp.status in (400, 401)
                    and err_code in PERMANENT_OAUTH_ERRORS
                )
                logger.error(
                    f"Token refresh failed: status={resp.status} error={err_code!r} "
                    f"body={body[:200]} permanent={permanent}"
                )
                return None, permanent
    except asyncio.TimeoutError:
        logger.warning("Token refresh timed out")
        return None, False
    except aiohttp.ClientError as e:
        logger.warning(f"Token refresh network error: {e}")
        return None, False
    except Exception as e:
        logger.error(f"Token refresh unexpected error: {e}", exc_info=True)
        return None, False
