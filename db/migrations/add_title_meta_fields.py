import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = [
    ("users", "is_supporter", "BOOLEAN"),
    ("user_best_scores", "status", "VARCHAR(20)"),
    ("user_best_scores", "ranked_date", "DATETIME"),
    ("user_map_attempts", "status", "VARCHAR(20)"),
    ("user_map_attempts", "ranked_date", "DATETIME"),
]

async def run_title_meta_fields_migration(engine):
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
