"""
Migration: add users.last_full_update.

The background updater now runs two passes at different cadences: a cheap
stats-only sweep every few minutes (which keeps the leaderboard live) and the
expensive full refresh (best scores + titles) far less often. Both go through
sync_user_stats_from_api, which bumps last_api_update — so the full pass needs
its own timestamp or it would never come due again.

Additive. Safe for SQLite — checks column existence before ALTER.
"""

import logging

from sqlalchemy import text

from db.migrations._utils import existing_columns

logger = logging.getLogger(__name__)


async def run_last_full_update_migration(engine):
    """Add users.last_full_update. Idempotent."""
    async with engine.begin() as conn:
        if "last_full_update" not in await existing_columns(conn, "users"):
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_full_update DATETIME"))
            logger.info("Migration: added column users.last_full_update")
