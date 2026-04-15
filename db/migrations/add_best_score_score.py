"""
Migration: add score column to user_best_scores table.
Safe for SQLite — checks column existence before ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_best_score_score_migration(engine):
    """Add score to user_best_scores table. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(user_best_scores)"))
        existing = {row[1] for row in result.fetchall()}

        if "score" not in existing:
            await conn.execute(
                text("ALTER TABLE user_best_scores ADD COLUMN score BIGINT")
            )
            logger.info("Migration: added column user_best_scores.score")
        else:
            logger.debug("Migration: column user_best_scores.score already exists")
