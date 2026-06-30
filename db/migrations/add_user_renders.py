"""
Migration: add user_renders table (per-user replay-render library —
file_id + metadata snapshot, for the /settings "Мои рендеры" picker). Idempotent.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_user_renders_migration(engine):
    async with engine.begin() as conn:
        exists = (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_renders'"
        ))).fetchone()
        if exists:
            return
        await conn.execute(text(
            """
            CREATE TABLE user_renders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ref VARCHAR(80) NOT NULL,
                file_id VARCHAR(512) NOT NULL,
                label VARCHAR(255) NOT NULL DEFAULT '',
                meta TEXT,
                created_at DATETIME NOT NULL,
                UNIQUE (user_id, ref)
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX ix_user_renders_user_id ON user_renders(user_id)"
        ))
        logger.info("Migration: created table user_renders")
