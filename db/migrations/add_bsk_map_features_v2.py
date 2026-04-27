"""Migration: add rhythm_complexity, slider_density, jump_density, note_count, duration to bsk_map_pool."""
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_NEW_COLUMNS = [
    ("f_rhythm_complexity", "REAL"),
    ("f_slider_density",    "REAL"),
    ("f_jump_density",      "REAL"),
    ("f_note_count",        "INTEGER"),
    ("f_duration",          "INTEGER"),
]


async def run_bsk_map_features_v2_migration(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        existing = {
            row[1]
            for row in (await conn.execute(text("PRAGMA table_info(bsk_map_pool)"))).fetchall()
        }
        for col_name, col_type in _NEW_COLUMNS:
            if col_name not in existing:
                await conn.execute(
                    text(f"ALTER TABLE bsk_map_pool ADD COLUMN {col_name} {col_type}")
                )
                logger.info(f"add_bsk_map_features_v2: added column bsk_map_pool.{col_name}")
