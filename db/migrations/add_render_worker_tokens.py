import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_render_worker_tokens_migration(engine):
    """One row per machine allowed to render, holding a hash and never a token.

    Replaces the single shared `RENDER_WORKER_TOKEN`, which stays valid so that
    machines set up before this go on working — nobody is made to re-enrol by
    a deploy.
    """
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS render_worker_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                digest      TEXT NOT NULL UNIQUE,
                issued_to   INTEGER,
                issued_name TEXT,
                worker      TEXT,
                created_at  TIMESTAMP NOT NULL,
                last_seen   TIMESTAMP,
                revoked_at  TIMESTAMP
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_render_worker_tokens_digest "
            "ON render_worker_tokens (digest)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_render_worker_tokens_issued_to "
            "ON render_worker_tokens (issued_to)"
        ))
        logger.info("Migration: render_worker_tokens table ensured")
