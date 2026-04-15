"""
Migration: add last_unlink_at column to users table.
Safe for SQLite — checks column existence before ALTER TABLE.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_user_unlink_at_migration(engine):
    """Add last_unlink_at to users table. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(users)"))
        existing = {row[1] for row in result.fetchall()}

        if "last_unlink_at" not in existing:
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_unlink_at DATETIME"))
            logger.info("Migration: added column users.last_unlink_at")
        else:
            logger.debug("Migration: column users.last_unlink_at already exists")
