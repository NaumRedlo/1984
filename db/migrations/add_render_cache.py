"""
Migration: add render_cache table (replay -> Telegram file_id).
Safe for SQLite — checks before CREATE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_render_cache_migration(engine):
    """Create render_cache table. Idempotent."""
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='render_cache'"
        ))
        if not result.fetchone():
            await conn.execute(text("""
                CREATE TABLE render_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key VARCHAR(255) NOT NULL UNIQUE,
                    file_id VARCHAR(512) NOT NULL,
                    created_at DATETIME NOT NULL
                )
            """))
            await conn.execute(text(
                "CREATE INDEX ix_render_cache_cache_key ON render_cache(cache_key)"
            ))
            logger.info("Migration: created table render_cache")
