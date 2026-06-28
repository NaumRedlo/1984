"""
Migration: users.was_supporter — a latched "ever an osu!supporter" flag.

`is_supporter` reflects *current* status (it drives the profile supporter badge),
so it can't be used to make the "Volunteer" title permanent. This flag latches to
True the moment any stats-sync observes support and is never cleared, so Volunteer
is earned once and kept forever even after the subscription lapses.

Additive, defaults False. Safe for SQLite — checks column existence before ALTER.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = [
    ("users", "was_supporter", "BOOLEAN DEFAULT 0"),
]


async def run_was_supporter_field_migration(engine):
    """Add users.was_supporter. Idempotent."""
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
