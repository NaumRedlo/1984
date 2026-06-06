"""Migration test: at most one OPEN submission per (bounty, user).

Guards the double-accept → double-payout race (see
db/migrations/add_submission_open_unique.py). Self-contained: spins up a temp
SQLite file, seeds duplicate/edge rows, runs the migration (twice, for
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

from db.migrations.add_submission_open_unique import (
    run_submission_open_unique_migration,
)


async def _exercise() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with eng.begin() as c:
            await c.execute(text(
                "CREATE TABLE submissions("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "bounty_id TEXT NOT NULL, user_id INTEGER NOT NULL,"
                "telegram_id INTEGER, status TEXT NOT NULL)"
            ))
            seed = [
                ("B1", 5, "tracking"), ("B1", 5, "tracking"),   # collapse → 1
                ("B2", 9, "tracking"), ("B2", 9, "pending"),    # collapse → 1
                ("B3", 7, "approved"), ("B3", 7, "tracking"),   # both survive
                ("B4", 1, "rejected"),                          # untouched
            ]
            for b, u, s in seed:
                await c.execute(text(
                    "INSERT INTO submissions(bounty_id,user_id,status) "
                    "VALUES(:b,:u,:s)"
                ), {"b": b, "u": u, "s": s})

        # Idempotent: running twice must not error or further mutate.
        await run_submission_open_unique_migration(eng)
        await run_submission_open_unique_migration(eng)

        async with eng.begin() as c:
            open_counts = (await c.execute(text(
                "SELECT bounty_id,user_id,COUNT(*) FROM submissions "
                "WHERE status IN ('tracking','pending') "
                "GROUP BY bounty_id,user_id"
            ))).all()
            assert all(n == 1 for *_, n in open_counts), open_counts

            # The approved row in B3 is preserved alongside its one open row.
            b3 = (await c.execute(text(
                "SELECT status FROM submissions WHERE bounty_id='B3' "
                "ORDER BY id"
            ))).scalars().all()
            assert b3 == ["approved", "tracking"], b3

            # A second OPEN row for an existing (bounty,user) is now rejected.
            with pytest.raises(IntegrityError):
                await c.execute(text(
                    "INSERT INTO submissions(bounty_id,user_id,status) "
                    "VALUES('B1',5,'tracking')"
                ))

        # …but a non-open (rejected) row for the same key is still allowed.
        async with eng.begin() as c:
            await c.execute(text(
                "INSERT INTO submissions(bounty_id,user_id,status) "
                "VALUES('B1',5,'rejected')"
            ))
    finally:
        await eng.dispose()
        os.unlink(path)


def test_open_submission_uniqueness_and_dedup():
    asyncio.run(_exercise())
