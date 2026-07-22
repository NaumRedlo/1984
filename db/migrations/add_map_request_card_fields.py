"""
Migration: bpm / length on map_requests (for the rendered request card).

map_requests already snapshots artist/title/version/star_rating; the image card
also shows BPM and length pills, so store those too. Additive, checked before
ALTER (SQLite-safe). New rows fill these at creation; older rows leave them NULL
(the card simply omits the missing pill).
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = [
    ("map_requests", "bpm", "FLOAT"),
    ("map_requests", "length", "INTEGER"),
    ("map_requests", "map_max_combo", "INTEGER"),
]


async def run_map_request_card_fields_migration(engine):
    """Add bpm / length on map_requests. Idempotent."""
    async with engine.begin() as conn:
        for table, column, sqltype in _COLUMNS:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))
                logger.info(f"Migration: added column {table}.{column}")
            else:
                logger.debug(f"Migration: column {table}.{column} already exists")
