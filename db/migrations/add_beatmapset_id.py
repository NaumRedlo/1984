"""
Migration: add beatmapset_id and creator columns to user_best_scores table.
Safe for SQLite — checks column existence before ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

NEW_COLUMNS = [
    ("beatmapset_id", "INTEGER"),
    ("creator", "TEXT"),
]


async def run_beatmapset_id_migration(engine):
    """Add beatmapset_id and creator to user_best_scores. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(user_best_scores)"))
        existing = {row[1] for row in result.fetchall()}

        for col_name, col_def in NEW_COLUMNS:
            if col_name not in existing:
                await conn.execute(
                    text(f"ALTER TABLE user_best_scores ADD COLUMN {col_name} {col_def}")
                )
                logger.info(f"Migration: added column user_best_scores.{col_name}")
            else:
                logger.debug(f"Migration: column user_best_scores.{col_name} already exists")
