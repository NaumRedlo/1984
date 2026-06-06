"""Migration: enforce at most one OPEN submission per (bounty, user).

The accept flow (`bounty.handlers._do_accept`) guards against re-entry with a
read-then-write duplicate check, but nothing serialised it: two concurrent
`/accept` calls (or a double-tapped inline accept button — aiogram dispatches
each update in its own task) could both pass the check and insert two
``tracking`` rows for the same ``(bounty_id, user_id)``.  The auto-checker then
iterates tracking submissions row-by-row and re-checks freshness only by the
row's own id, so BOTH rows get approved and HP is credited twice.

This adds a partial UNIQUE index over the OPEN statuses (``tracking`` /
``pending``) so a duplicate open row fails loudly at the DB level.  ``approved``
is deliberately excluded: a player can legitimately keep one approved row, and
including it would make this migration fail on any DB that already accumulated
duplicate approvals from the bug (those represent already-credited HP and are
left as historical artifacts).  The award-time in-transaction guard in the
auto-checker / replay upload is the second line of defence.

Existing duplicate open rows are collapsed first (keep the earliest, expire the
rest) so the UNIQUE index can be created.  Idempotent.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_submission_open_unique_migration(engine):
    """Collapse duplicate open submissions, then add the partial unique index."""
    async with engine.begin() as conn:
        # Collapse pre-existing duplicates: for each (bounty_id, user_id) with
        # more than one open row, keep the earliest (min id) and expire the
        # rest.  Open rows carry no awarded HP, so expiring extras is safe.
        result = await conn.execute(text(
            """
            UPDATE submissions
               SET status = 'expired'
             WHERE status IN ('tracking', 'pending')
               AND id NOT IN (
                   SELECT MIN(id)
                     FROM submissions
                    WHERE status IN ('tracking', 'pending')
                    GROUP BY bounty_id, user_id
               )
            """
        ))
        if result.rowcount:
            logger.info(
                "Migration: collapsed %d duplicate open submission(s) before "
                "adding the unique index", result.rowcount,
            )

        await conn.execute(text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_submissions_open
                ON submissions(bounty_id, user_id)
             WHERE status IN ('tracking', 'pending')
            """
        ))
        logger.info(
            "Migration: ensured unique open submission per (bounty_id, user_id)"
        )
