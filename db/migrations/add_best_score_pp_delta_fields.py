import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = [
    ("user_best_scores", "previous_pp", "FLOAT"),
    ("user_best_scores", "pp_changed_at", "DATETIME"),
    ("users", "best_scores_baseline_at", "DATETIME"),
]

async def run_best_score_pp_delta_fields_migration(engine):
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
