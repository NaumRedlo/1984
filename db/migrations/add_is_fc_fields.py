import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

TABLES = ("user_best_scores", "user_map_attempts")

async def run_is_fc_fields_migration(engine):
    async with engine.begin() as conn:
        for table in TABLES:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if "is_fc" not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN is_fc BOOLEAN"))
                logger.info(f"Migration: added column {table}.is_fc")
            else:
                logger.debug(f"Migration: column {table}.is_fc already exists")
