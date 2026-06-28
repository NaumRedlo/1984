"""
Migration: Batch II profile-stat fields on `users`.

osu! API user fields the bot fetched for the profile card but never stored, now
needed for bulk title checks:
- level           — osu! level.current ("Recruit": reach level 25)
- join_date       — account creation ("Citizen of Record": account older than 2y)
- grade_count_s   — S + SH ranks ("Serial Performer": 50 S ranks)
- grade_count_ss  — SS + SSH ranks ("Five Collector": 100 SS ranks)

All additive, backfilled on the next stats-sync. Safe for SQLite — checks column
existence before each ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = [
    ("users", "level", "INTEGER DEFAULT 0"),
    ("users", "join_date", "DATETIME"),
    ("users", "grade_count_s", "INTEGER DEFAULT 0"),
    ("users", "grade_count_ss", "INTEGER DEFAULT 0"),
]


async def run_batch2_profile_stats_migration(engine):
    """Add the Batch II profile-stat columns. Idempotent."""
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
