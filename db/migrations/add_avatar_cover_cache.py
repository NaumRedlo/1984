"""
Migration: add avatar_data, cover_data BLOB columns to users table.
Safe for SQLite — checks column existence before ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

NEW_COLUMNS = [
    ("avatar_data", "BLOB"),
    ("cover_data", "BLOB"),
]


async def run_avatar_cache_migration(engine):
    """Add avatar_data/cover_data BLOB columns to users table. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(users)"))
        existing = {row[1] for row in result.fetchall()}

        for col_name, col_def in NEW_COLUMNS:
            if col_name not in existing:
                stmt = f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
                await conn.execute(text(stmt))
                logger.info(f"Migration: added column users.{col_name}")
            else:
                logger.debug(f"Migration: column users.{col_name} already exists")
