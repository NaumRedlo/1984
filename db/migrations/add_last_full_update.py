import logging

from sqlalchemy import text

from db.migrations._utils import existing_columns

logger = logging.getLogger(__name__)

async def run_last_full_update_migration(engine):
    async with engine.begin() as conn:
        if "last_full_update" not in await existing_columns(conn, "users"):
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_full_update DATETIME"))
            logger.info("Migration: added column users.last_full_update")
