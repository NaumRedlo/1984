"""The migration chain must survive a database that never had the removed
features' tables — and must still upgrade one that does.

`bounties` and `submissions` belong to features deleted long ago. Their
migrations guarded on a COLUMN being absent, but `PRAGMA table_info(<missing
table>)` returns an empty result set instead of raising, so the guard passed and
the following statement died with "no such table" — meaning a brand-new
deployment crashed during startup migrations.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from db.database import Base
import db.models  # noqa: F401 — register every table
from db.migrations import run_all_migrations

_LEGACY_USER = (
    "INSERT INTO users (id, chat_id, telegram_id, osu_username, rank, hps_points,"
    " season_bonus_hps, bounties_participated, duel_wins, duel_losses, bp,"
    " duel_user_aim, duel_user_speed, duel_user_acc, duel_user_cons,"
    " created_at, updated_at)"
    " VALUES (1, -100, 1, 'legacy', 'Candidate', 0, 0, 0, 0, 0, 0,"
    " 4, 4, 4, 4, '2020-01-01', '2020-01-01')"
)


async def _fresh_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


async def test_chain_runs_on_a_database_without_the_removed_tables():
    engine = await _fresh_engine()
    try:
        # Twice: the chain must also be idempotent.
        await run_all_migrations(engine)
        await run_all_migrations(engine)
    finally:
        await engine.dispose()


async def test_chain_still_upgrades_a_legacy_database():
    engine = await _fresh_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE bounties (id INTEGER PRIMARY KEY)"))
            await conn.execute(text(
                "CREATE TABLE submissions (id INTEGER PRIMARY KEY, user_id INTEGER,"
                " status TEXT, submitted_at DATETIME)"))
            await conn.execute(text(_LEGACY_USER))
            await conn.execute(text(
                "INSERT INTO submissions (user_id, status, submitted_at) VALUES"
                " (1, 'approved', '2021-05-05 10:00:00'),"
                " (1, 'approved', '2022-01-01 10:00:00')"))

        await run_all_migrations(engine)

        async with engine.begin() as conn:
            bounties = {r[1] for r in (await conn.execute(text("PRAGMA table_info(bounties)"))).fetchall()}
            submissions = {r[1] for r in (await conn.execute(text("PRAGMA table_info(submissions)"))).fetchall()}
            first_approved = (await conn.execute(
                text("SELECT first_approved_at FROM users WHERE id = 1"))).scalar()

        assert "reminder_sent" in bounties
        assert {"n_300", "n_100", "n_50", "ur_est"} <= submissions
        # Backfilled from the OLDEST approved submission, not the newest.
        assert str(first_approved).startswith("2021-05-05")
    finally:
        await engine.dispose()
