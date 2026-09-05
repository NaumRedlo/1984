import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

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
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
