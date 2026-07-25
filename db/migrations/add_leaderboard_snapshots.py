"""
Migration: add leaderboard_snapshots table (weekly delta leaderboard anchors).
Safe for SQLite — uses checkfirst creation.
"""

import logging

from db.models.leaderboard_snapshot import LeaderboardSnapshot

logger = logging.getLogger(__name__)


async def run_leaderboard_snapshots_migration(engine):
    """Create leaderboard_snapshots table if missing."""
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: LeaderboardSnapshot.__table__.create(sync_conn, checkfirst=True)
        )
        logger.debug("Migration: ensured table leaderboard_snapshots exists")
