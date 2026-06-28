"""
Migration: completion-percent fields on user_map_attempts.

A failed play's completion % = (count_300 + count_100 + count_50 + count_miss) /
total_objects — exactly what the recent card already computes. We stored 100/50/miss
but not count_300 or the map object count, so add them to enable the "Last Note"
title (fail a map after completing 95%+). Both come straight from the API
(score statistics + beatmap count_circles/sliders/spinners), backfilled lazily on
the next recent-play sync.

Additive. Safe for SQLite — checks column existence before ALTER.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = [
    ("user_map_attempts", "count_300", "INTEGER"),
    ("user_map_attempts", "total_objects", "INTEGER"),
]


async def run_completion_fields_migration(engine):
    """Add count_300 / total_objects on user_map_attempts. Idempotent."""
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
