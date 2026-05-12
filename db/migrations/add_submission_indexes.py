"""Migration: add indexes on Submission.user_id and Submission.status.

Both columns are queried on hot paths: status on every /review (admin pulls
pending), user_id on every /submit (duplicate check). Without indexes SQLite
full-scans the table on each call.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_submission_indexes_migration(engine):
    """Create indexes if missing. Idempotent."""
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_submissions_user_id ON submissions(user_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_submissions_status ON submissions(status)"
        ))
        logger.info("Migration: ensured submissions(user_id) and submissions(status) indexes")
