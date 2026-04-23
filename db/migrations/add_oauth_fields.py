"""
Migration: add OAuth token fields to users table.
- users.oauth_access_token (VARCHAR)
- users.oauth_refresh_token (VARCHAR)
- users.oauth_token_expiry (DATETIME)
Safe for SQLite — checks before ALTER.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_oauth_migration(engine):
    """Add OAuth columns to users. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(users)"))
        cols = {row[1] for row in result.fetchall()}

        if "oauth_access_token" not in cols:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN oauth_access_token VARCHAR(512)"
            ))
            logger.info("Migration: added column users.oauth_access_token")

        if "oauth_refresh_token" not in cols:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN oauth_refresh_token VARCHAR(512)"
            ))
            logger.info("Migration: added column users.oauth_refresh_token")

        if "oauth_token_expiry" not in cols:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN oauth_token_expiry DATETIME"
            ))
            logger.info("Migration: added column users.oauth_token_expiry")
