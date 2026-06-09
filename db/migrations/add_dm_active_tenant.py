import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_dm_active_tenant_migration(engine):
    """Ensure the ``dm_active_tenant`` table exists.

    Stores, per Telegram identity, which group's data the bot shows that user in
    a private chat. Additive and idempotent (``CREATE TABLE IF NOT EXISTS``);
    create_all also covers a fresh DB, this guarantees the table on upgrades.
    """
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dm_active_tenant (
                telegram_id BIGINT PRIMARY KEY,
                chat_id     BIGINT NOT NULL,
                updated_at  DATETIME NOT NULL
            )
        """))
        logger.info("Migration: dm_active_tenant table ensured")
