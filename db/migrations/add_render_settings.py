"""
Migration: add user_render_settings table.
Safe for SQLite — checks before CREATE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_render_settings_migration(engine):
    """Create user_render_settings table. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_render_settings'"
        ))
        if not result.fetchone():
            await conn.execute(text("""
                CREATE TABLE user_render_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                    skin VARCHAR(255) NOT NULL DEFAULT 'default',
                    resolution VARCHAR(20) NOT NULL DEFAULT '1280x720',
                    cursor_size FLOAT NOT NULL DEFAULT 1.0,
                    cursor_trail BOOLEAN NOT NULL DEFAULT 1,
                    show_pp_counter BOOLEAN NOT NULL DEFAULT 1,
                    show_scoreboard BOOLEAN NOT NULL DEFAULT 0,
                    show_key_overlay BOOLEAN NOT NULL DEFAULT 1,
                    show_hit_error_meter BOOLEAN NOT NULL DEFAULT 1,
                    show_mods BOOLEAN NOT NULL DEFAULT 1,
                    show_result_screen BOOLEAN NOT NULL DEFAULT 1,
                    bg_dim INTEGER NOT NULL DEFAULT 80
                )
            """))
            await conn.execute(text(
                "CREATE INDEX ix_user_render_settings_user_id ON user_render_settings(user_id)"
            ))
            logger.info("Migration: created table user_render_settings")
