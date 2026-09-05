import logging

from db.models.map_attempt import UserMapAttempt

logger = logging.getLogger(__name__)

async def run_map_attempts_migration(engine):
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: UserMapAttempt.__table__.create(sync_conn, checkfirst=True))
        logger.debug("Migration: ensured table user_map_attempts exists")
