"""
Migration: Wave-4 title logging-subsystem fields (all on `users`).

The Wave-4 titles need state the osu! API doesn't carry — open/compare counters,
a daily-activity streak, a weekly play_count delta, and a 180-day comeback flag:

- profile_opens_date/count/best      — "Still Here" (open own profile 5x/day)
- compare_uses                       — "Informant" (use /compare on others 50x)
- active_day/streak/streak_best      — "Sleepless Watch" (30 active days in a row)
- playcount_week_anchor[/_at], week_plays_best — "Stakhanovite" (500 plays/week)
- comeback_done                      — "quit w" (return after 180+ days silent)

All additive with sane defaults. Safe for SQLite — checks column existence before
each ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

# (table, column, SQL type)
_COLUMNS = [
    ("users", "profile_opens_date", "DATE"),
    ("users", "profile_opens_count", "INTEGER DEFAULT 0"),
    ("users", "profile_opens_best", "INTEGER DEFAULT 0"),
    ("users", "compare_uses", "INTEGER DEFAULT 0"),
    ("users", "active_day", "DATE"),
    ("users", "active_streak", "INTEGER DEFAULT 0"),
    ("users", "active_streak_best", "INTEGER DEFAULT 0"),
    ("users", "playcount_week_anchor", "INTEGER"),
    ("users", "playcount_week_anchor_at", "DATETIME"),
    ("users", "week_plays_best", "INTEGER DEFAULT 0"),
    ("users", "comeback_done", "BOOLEAN DEFAULT 0"),
]


async def run_w4_logging_fields_migration(engine):
    """Add the Wave-4 title logging columns. Idempotent."""
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
