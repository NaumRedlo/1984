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

        # `bounties` belongs to a feature that was removed; the table survives
        # only on databases old enough to have had it. On a fresh install it is
        # absent, so the ALTER below must be skipped — see _utils.table_exists
        # for why a column check alone isn't enough to catch that.
        if not await table_exists(conn, "bounties"):
            logger.debug("Migration: no bounties table — skipping reminder_sent")
            return

        if "reminder_sent" not in await existing_columns(conn, "bounties"):
            await conn.execute(text(
                "ALTER TABLE bounties ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0"
            ))
            logger.info("Migration: added column bounties.reminder_sent")
