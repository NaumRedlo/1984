import logging
from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists

logger = logging.getLogger(__name__)

async def run_bot_settings_migration(engine):
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """))
        logger.info("Migration: bot_settings table ensured")

        if not await table_exists(conn, "bounties"):
            logger.debug("Migration: no bounties table — skipping reminder_sent")
            return

        if "reminder_sent" not in await existing_columns(conn, "bounties"):
            await conn.execute(text(
                "ALTER TABLE bounties ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0"
            ))
            logger.info("Migration: added column bounties.reminder_sent")
