"""
Migration: effective-difficulty fields on both score tables.

Batch II's hardest titles gate on mod-adjusted difficulty, which the bot didn't
keep:
- ar      — base approach rate; effective AR (via apply_mods) drives "Heavy Hand"
- eff_sr  — mod-adjusted star rating (from osu! API beatmap attributes WITH mods),
            for "Double Digit Threat" / "Watchmaker" / "Double Sentence"

Both additive and backfilled lazily as users re-sync (eff_sr falls back to the
nominal star rating when no speed/diff mod applies or the API call fails). Safe
for SQLite — checks column existence before each ALTER TABLE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = [
    ("user_best_scores", "ar", "FLOAT"),
    ("user_best_scores", "eff_sr", "FLOAT"),
    ("user_map_attempts", "ar", "FLOAT"),
    ("user_map_attempts", "eff_sr", "FLOAT"),
]


async def run_effective_fields_migration(engine):
    """Add ar / eff_sr on both score tables. Idempotent."""
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
