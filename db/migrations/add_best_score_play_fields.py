"""
Migration: add per-play fields to user_best_scores (Phase B1 titles).

Adds bpm / length / map_max_combo / count_100 / count_50 / count_miss — all
already present in the best-scores API response, just not previously stored.
Backfilled lazily as each user's scores are re-synced. Safe for SQLite —
checks column existence before ALTER TABLE.
"""

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
]


async def run_best_score_play_fields_migration(engine):
    """Add Phase B1 per-play columns to user_best_scores. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(user_best_scores)"))
        existing = {row[1] for row in result.fetchall()}

        for col_name, col_def in NEW_COLUMNS:
            if col_name not in existing:
                await conn.execute(
                    text(f"ALTER TABLE user_best_scores ADD COLUMN {col_name} {col_def}")
                )
                logger.info(f"Migration: added column user_best_scores.{col_name}")
            else:
                logger.debug(f"Migration: column user_best_scores.{col_name} already exists")
