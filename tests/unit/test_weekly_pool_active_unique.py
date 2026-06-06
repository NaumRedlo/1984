"""Migration test: at most one ACTIVE weekly bounty pool.

Guards the concurrent-regen race (see
db/migrations/add_weekly_pool_active_unique.py). Self-contained: spins up a
temp SQLite file, seeds duplicate active rows, runs the migration (twice, for
idempotency), and asserts the dedup + partial-unique-index behaviour. No
pytest-asyncio needed — the async body runs under asyncio.run().
"""

import asyncio
import os
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from db.migrations.add_weekly_pool_active_unique import (
    run_weekly_pool_active_unique_migration,
)


async def _exercise() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with eng.begin() as c:
            await c.execute(text(
                "CREATE TABLE weekly_bounty_pool("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "week_number INTEGER NOT NULL,"
                "started_at TEXT, ends_at TEXT,"
                "is_active INTEGER NOT NULL)"
            ))
            # Three active rows (the race result) + one already-inactive one.
            seed = [(1, 0), (2, 1), (3, 1), (4, 1)]
            for wn, act in seed:
                await c.execute(text(
                    "INSERT INTO weekly_bounty_pool(week_number,is_active) "
                    "VALUES(:w,:a)"
                ), {"w": wn, "a": act})

        # Idempotent: running twice must not error or further mutate.
        await run_weekly_pool_active_unique_migration(eng)
        await run_weekly_pool_active_unique_migration(eng)

        async with eng.begin() as c:
            rows = (await c.execute(text(
                "SELECT id, week_number, is_active FROM weekly_bounty_pool "
                "ORDER BY id"
            ))).all()
            active = [r for r in rows if r[2] == 1]
            # Exactly one active row survives — the newest by id (week 4).
            assert len(active) == 1, rows
            assert active[0][0] == 4, rows

            # A second active insert is now rejected at the DB level.
            with pytest.raises(IntegrityError):
                await c.execute(text(
                    "INSERT INTO weekly_bounty_pool(week_number,is_active) "
                    "VALUES(99,1)"
                ))

        # …but additional INACTIVE rows are always allowed (history).
        async with eng.begin() as c:
            await c.execute(text(
                "INSERT INTO weekly_bounty_pool(week_number,is_active) "
                "VALUES(98,0)"
            ))
    finally:
        await eng.dispose()
        os.unlink(path)


def test_active_weekly_pool_uniqueness_and_dedup():
    asyncio.run(_exercise())
