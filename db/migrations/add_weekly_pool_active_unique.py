"""Migration: enforce at most ONE active weekly bounty pool.

`weekly_generator.generate_weekly_pool` closes the current active pool (flips
the single ``is_active=1`` row to ``0``) and then inserts a fresh ``is_active=1``
row.  Three callers can drive that flow — the Monday cron, the startup
bootstrap, and the admin ``/regenpool confirm`` — and nothing serialised the
SELECT-then-INSERT, so two of them racing could leave TWO active pools.  The
rendering/bootstrap layer then does ``.scalar_one_or_none()`` on
``is_active == 1`` and raises ``MultipleResultsFound``.

A module-level ``asyncio.Lock`` + idempotency guard in the generator is the
first line of defence; this partial UNIQUE index is the DB-level backstop so a
second active pool fails loudly rather than silently corrupting the invariant.

Any pre-existing duplicate active rows are collapsed first (keep the newest by
id — that's the most recently generated pool — set the rest ``is_active=0``) so
the UNIQUE index can be created.  Idempotent.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_weekly_pool_active_unique_migration(engine):
    """Collapse duplicate active pools, then add the partial unique index."""
    async with engine.begin() as conn:
        # Collapse pre-existing duplicates: if more than one row is active,
        # keep the newest (max id — the most recently generated pool) and
        # deactivate the rest.
        result = await conn.execute(text(
            """
            UPDATE weekly_bounty_pool
               SET is_active = 0
             WHERE is_active = 1
               AND id NOT IN (
                   SELECT MAX(id)
                     FROM weekly_bounty_pool
                    WHERE is_active = 1
               )
            """
        ))
        if result.rowcount:
            logger.info(
                "Migration: collapsed %d duplicate active weekly pool(s) "
                "before adding the unique index", result.rowcount,
            )

        await conn.execute(text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_weekly_pool_active
                ON weekly_bounty_pool(is_active)
             WHERE is_active = 1
            """
        ))
        logger.info(
            "Migration: ensured at most one active weekly bounty pool"
        )
