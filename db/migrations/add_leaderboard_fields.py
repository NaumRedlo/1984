"""
Migration: add play_time, ranked_score, total_hits columns to users table.
Safe for SQLite — checks column existence before ALTER TABLE.
Backfills NULL → 0 for existing rows.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

NEW_COLUMNS = [
    ("play_time", "INTEGER DEFAULT 0"),
    ("ranked_score", "BIGINT DEFAULT 0"),
    ("total_hits", "BIGINT DEFAULT 0"),
]


async def run_migration(engine):
    """Add missing columns to users table and backfill NULLs. Idempotent."""
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

        # Backfill NULLs → 0 for existing rows (SQLite ALTER doesn't apply DEFAULT to old rows)
        for col_name, _ in NEW_COLUMNS:
            await conn.execute(
                text(f"UPDATE users SET {col_name} = 0 WHERE {col_name} IS NULL")
            )
