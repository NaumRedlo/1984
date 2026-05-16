import logging
from sqlalchemy import text

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

        result = await conn.execute(text("PRAGMA table_info(bounties)"))
        existing = {row[1] for row in result.fetchall()}
        if "reminder_sent" not in existing:
            await conn.execute(text(
                "ALTER TABLE bounties ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0"
            ))
            logger.info("Migration: added column bounties.reminder_sent")
