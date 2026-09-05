import logging

from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists

logger = logging.getLogger(__name__)

async def run_user_first_approved_at_migration(engine) -> None:
    async with engine.begin() as conn:

        if "first_approved_at" not in await existing_columns(conn, "users"):
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN first_approved_at DATETIME NULL"
            ))

        if not await table_exists(conn, "submissions"):
            logger.debug("Migration: no submissions table — skipping first_approved_at backfill")
            return

        await conn.execute(text("""
            UPDATE users
            SET first_approved_at = (
                SELECT MIN(submitted_at)
                FROM submissions
                WHERE submissions.user_id = users.id
                  AND submissions.status = 'approved'
            )
            WHERE first_approved_at IS NULL
              AND EXISTS (
                SELECT 1 FROM submissions
                WHERE submissions.user_id = users.id
                  AND submissions.status = 'approved'
              )
        """))
