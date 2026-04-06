"""
Migration: add total_score column to users table.
Safe for SQLite — checks column existence before ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_total_score_migration(engine):
    """Add total_score to users table. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(users)"))
        existing = {row[1] for row in result.fetchall()}

        if "total_score" not in existing:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN total_score BIGINT DEFAULT 0")
            )
            logger.info("Migration: added column users.total_score")
            await conn.execute(
                text("UPDATE users SET total_score = 0 WHERE total_score IS NULL")
            )
        else:
            logger.debug("Migration: column users.total_score already exists")
