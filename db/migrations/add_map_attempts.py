"""
Migration: add user_map_attempts table.
Safe for SQLite — uses checkfirst creation.
"""

import logging

from db.models.map_attempt import UserMapAttempt

logger = logging.getLogger(__name__)


async def run_map_attempts_migration(engine):
    """Create user_map_attempts table if missing."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: UserMapAttempt.__table__.create(sync_conn, checkfirst=True))
        logger.debug("Migration: ensured table user_map_attempts exists")
