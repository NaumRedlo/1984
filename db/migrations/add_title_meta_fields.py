"""
Migration: metadata fields for Wave-3 titles.

- users.is_supporter        — osu!supporter flag (title "Volunteer")
- {best,attempts}.status    — beatmap status string (title "Graveyard Tourist")
- {best,attempts}.ranked_date — beatmapset ranked date (title "Archaeologist")

All additive and backfilled lazily on the next stats / score re-sync. Safe for
SQLite — checks column existence before each ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

# (table, column, SQL type)
_COLUMNS = [
    ("users", "is_supporter", "BOOLEAN"),
    ("user_best_scores", "status", "VARCHAR(20)"),
    ("user_best_scores", "ranked_date", "DATETIME"),
    ("user_map_attempts", "status", "VARCHAR(20)"),
    ("user_map_attempts", "ranked_date", "DATETIME"),
]


async def run_title_meta_fields_migration(engine):
    """Add the Wave-3 title metadata columns. Idempotent."""
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
