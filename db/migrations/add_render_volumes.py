"""
Migration: add music_volume / hitsound_volume (0-100 %) to user_render_settings.
Additive, idempotent (checks existing columns).
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

# column -> SQLite default literal
_NEW_COLUMNS = {
    "music_volume": "100",
    "hitsound_volume": "100",
}


async def run_render_volumes_migration(engine):
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(user_render_settings)"))
        existing = {row[1] for row in result.fetchall()}
        if not existing:
            return  # table not created yet; create_all handles a fresh DB
        for col, default in _NEW_COLUMNS.items():
            if col not in existing:
                await conn.execute(text(
                    f"ALTER TABLE user_render_settings "
                    f"ADD COLUMN {col} INTEGER NOT NULL DEFAULT {default}"
                ))
                logger.info("Migration: added user_render_settings.%s", col)
