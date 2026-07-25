"""Migration: add User.first_approved_at column + backfill.

Plan: unified-giggling-tiger (step 5/9).

Adds a single nullable DATETIME column to `users` to mark the timestamp
of each user's first approved submission.  This anchors the bootstrap
multiplier B(t) used in hp_calculator: B(t) decays from ~1.5 down to 1.0
over the first 90 days of the user's HPS career, not their account age.

Backfill: for each existing user with at least one approved submission,
set first_approved_at = MIN(submitted_at) over approved submissions.
Users with no approved submissions yet are left NULL; the field is
populated when their first approval lands (in review.py, replay.py,
bounty_auto_checker.py).

Idempotent: checks pragma_table_info before ADD COLUMN, and the backfill
UPDATE only writes where first_approved_at IS NULL.

The `submissions` table the backfill reads from belongs to a removed feature
and is absent on a fresh database, so the backfill is skipped there — the
column is still added, it just starts out entirely NULL.
"""

import logging

from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists

logger = logging.getLogger(__name__)


async def run_user_first_approved_at_migration(engine) -> None:
    async with engine.begin() as conn:
        # Detect whether the column already exists (SQLite has no IF NOT
        # EXISTS for ALTER TABLE ADD COLUMN).
        if "first_approved_at" not in await existing_columns(conn, "users"):
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN first_approved_at DATETIME NULL"
            ))

        if not await table_exists(conn, "submissions"):
            logger.debug("Migration: no submissions table — skipping first_approved_at backfill")
            return

        # Backfill from oldest approved submission per user.  Only touches
        # rows where first_approved_at is still NULL — re-runs are no-ops.
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
