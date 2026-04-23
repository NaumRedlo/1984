"""
Migration: create oauth_tokens table and migrate existing tokens from users.
Encrypts plaintext tokens during migration, then NULLs old columns.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_oauth_tokens_migration(engine, encryption_key: str = None):
    """Create oauth_tokens table. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='oauth_tokens'"
        ))
        if result.fetchone():
            logger.debug("Migration: oauth_tokens table already exists, skipping")
            return

        await conn.execute(text("""
            CREATE TABLE oauth_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                access_token_enc BLOB NOT NULL,
                refresh_token_enc BLOB,
                token_expiry DATETIME,
                scopes VARCHAR(255),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        await conn.execute(text(
            "CREATE UNIQUE INDEX ix_oauth_tokens_user_id ON oauth_tokens(user_id)"
        ))
        logger.info("Migration: created table oauth_tokens")

        if encryption_key:
            from cryptography.fernet import Fernet
            from datetime import datetime, timezone

            fernet = Fernet(encryption_key.encode())
            result = await conn.execute(text(
                "SELECT id, oauth_access_token, oauth_refresh_token, oauth_token_expiry "
                "FROM users WHERE oauth_access_token IS NOT NULL"
            ))
            rows = result.fetchall()
            now = datetime.now(timezone.utc).isoformat()
            for row in rows:
                user_id, access_token, refresh_token, expiry = row
                access_enc = fernet.encrypt(access_token.encode())
                refresh_enc = fernet.encrypt(refresh_token.encode()) if refresh_token else None

                await conn.execute(text(
                    "INSERT INTO oauth_tokens "
                    "(user_id, access_token_enc, refresh_token_enc, token_expiry, scopes, created_at, updated_at) "
                    "VALUES (:uid, :acc, :ref, :exp, :scopes, :now, :now)"
                ), {
                    "uid": user_id, "acc": access_enc, "ref": refresh_enc,
                    "exp": expiry, "scopes": "public identify", "now": now,
                })

            if rows:
                await conn.execute(text(
                    "UPDATE users SET oauth_access_token = NULL, "
                    "oauth_refresh_token = NULL, oauth_token_expiry = NULL "
                    "WHERE oauth_access_token IS NOT NULL"
                ))
                logger.info(f"Migration: migrated {len(rows)} tokens to oauth_tokens (encrypted)")
