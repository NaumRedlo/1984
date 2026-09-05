import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

NEW_COLUMNS = [
    ("bpm", "REAL"),
    ("length", "INTEGER"),
    ("map_max_combo", "INTEGER"),
    ("count_100", "INTEGER"),
    ("count_50", "INTEGER"),
    ("count_miss", "INTEGER"),
    ("passed", "BOOLEAN"),
    ("played_at", "DATETIME"),
]

async def run_map_attempt_play_fields_migration(engine):
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(user_map_attempts)"))
        existing = {row[1] for row in result.fetchall()}

        for col_name, col_def in NEW_COLUMNS:
            if col_name not in existing:
                await conn.execute(
                    text(f"ALTER TABLE user_map_attempts ADD COLUMN {col_name} {col_def}")
                )
                logger.info(f"Migration: added column user_map_attempts.{col_name}")
            else:
                logger.debug(f"Migration: column user_map_attempts.{col_name} already exists")
