"""
Migration: add osu! API difficulty attributes + parsed .osu pattern feature
columns to bsk_map_pool.
"""
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_NEW_COLUMNS = [
    ("api_aim_diff",         "REAL"),
    ("api_speed_diff",       "REAL"),
    ("api_slider_factor",    "REAL"),
    ("api_speed_note_count", "REAL"),
    ("f_burst",              "REAL"),
    ("f_stream",             "REAL"),
    ("f_death_stream",       "REAL"),
    ("f_jump_vel",           "REAL"),
    ("f_back_forth",         "REAL"),
    ("f_angle_var",          "REAL"),
    ("f_sv_var",             "REAL"),
    ("f_density_var",        "REAL"),
]


async def run_bsk_map_features_migration(engine: AsyncEngine) -> None:
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
                logger.info(f"add_bsk_map_features: added column bsk_map_pool.{col_name}")
