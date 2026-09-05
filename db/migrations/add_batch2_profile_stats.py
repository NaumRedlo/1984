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
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
