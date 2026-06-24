"""
Migration: add is_fc (API perfect-combo flag) to user_best_scores and
user_map_attempts.

FC titles previously relied on comparing the player's combo to the map's max
combo, which fails when the map's max combo is missing from the payload or the
combo counts differ (lazer vs stable). The score object carries a direct
perfect-combo flag — capture it as the primary FC signal. Backfilled lazily on
re-sync. Safe for SQLite — checks column existence before ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

TABLES = ("user_best_scores", "user_map_attempts")


async def run_is_fc_fields_migration(engine):
    """Add is_fc to both score tables. Idempotent."""
    async with engine.begin() as conn:
        for table in TABLES:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if "is_fc" not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN is_fc BOOLEAN"))
                logger.info(f"Migration: added column {table}.is_fc")
            else:
                logger.debug(f"Migration: column {table}.is_fc already exists")
