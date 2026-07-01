"""
Migration: add strain-graph / hit-counter / seizure-warning toggles and the
skin-hitsounds flag to user_render_settings. Additive, idempotent (checks
existing columns).
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

# column -> SQLite default literal
_NEW_COLUMNS = {
    "show_strain_graph": "1",
    "show_hit_counter": "1",
    "show_seizure_warning": "0",
    "use_skin_hitsounds": "0",
    "cinema_mode": "0",
}


async def run_render_settings_extra_migration(engine):
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(user_render_settings)"))
        existing = {row[1] for row in result.fetchall()}
        if not existing:
            return  # table not created yet; create_all/base migration handles it
        for col, default in _NEW_COLUMNS.items():
            if col not in existing:
                await conn.execute(text(
                    f"ALTER TABLE user_render_settings "
                    f"ADD COLUMN {col} BOOLEAN NOT NULL DEFAULT {default}"
                ))
                logger.info("Migration: added user_render_settings.%s", col)
