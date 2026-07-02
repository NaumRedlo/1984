"""
Migration: pp-delta tracking for the top-plays card (`tpp`).

`user_best_scores.previous_pp` / `.pp_changed_at` let sync_user_best_scores()
record when a score's pp last changed and by how much, so the card can show
"+14pp 2 days ago" / "NEW" badges. `users.best_scores_baseline_at` marks the
first-ever sync for a user, so that initial snapshot doesn't make every one
of their existing 100 scores look "NEW".

Additive, all nullable. Safe for SQLite — checks column existence before ALTER.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = [
    ("user_best_scores", "previous_pp", "FLOAT"),
    ("user_best_scores", "pp_changed_at", "DATETIME"),
    ("users", "best_scores_baseline_at", "DATETIME"),
]


async def run_best_score_pp_delta_fields_migration(engine):
    """Add pp-delta tracking columns. Idempotent."""
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
