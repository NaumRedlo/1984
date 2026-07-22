"""
Migration: add map_requests table (player-to-player map challenges).
Safe for SQLite — uses checkfirst creation.
"""

import logging

from db.models.map_request import MapRequest

logger = logging.getLogger(__name__)


async def run_map_requests_migration(engine):
    """Create map_requests table if missing."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: MapRequest.__table__.create(sync_conn, checkfirst=True))
        logger.debug("Migration: ensured table map_requests exists")
