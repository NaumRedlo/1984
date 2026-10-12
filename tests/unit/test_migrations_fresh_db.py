import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from db.database import Base
import db.models
from db.migrations import run_all_migrations

_LEGACY_USER = (
    "INSERT INTO users (id, chat_id, telegram_id, osu_username, created_at, updated_at)"
    " VALUES (1, -100, 1, 'legacy', '2020-01-01', '2020-01-01')"
)

async def _fresh_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine

@pytest.mark.sqlite_only
async def test_chain_runs_on_a_database_without_the_removed_tables():
    engine = await _fresh_engine()
    try:

        await run_all_migrations(engine)
        await run_all_migrations(engine)
    finally:
        await engine.dispose()

@pytest.mark.sqlite_only
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

        assert str(first_approved).startswith("2021-05-05")
    finally:
        await engine.dispose()


async def test_old_chat_data_is_written_aside_and_then_its_columns_are_gone(tmp_path):
    import json

    from db.migrations._utils import existing_columns
    from db.migrations.drop_old_chat_data import run_drop_old_chat_data_migration

    engine = await _fresh_engine()
    kept = tmp_path / "old.json"
    try:
        assert await run_drop_old_chat_data_migration(engine, kept_in=str(kept)) == [], "a database made today has nothing to remove"
        assert not kept.exists()
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE users ADD COLUMN hps_points INTEGER DEFAULT 0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN duel_wins INTEGER DEFAULT 0"))
            await conn.execute(text('ALTER TABLE users ADD COLUMN "rank" VARCHAR(50)'))
            await conn.execute(text(_LEGACY_USER))
            await conn.execute(text("UPDATE users SET hps_points = 120, duel_wins = 4, rank = 'Candidate' WHERE id = 1"))
        removed = await run_drop_old_chat_data_migration(engine, kept_in=str(kept))
        assert sorted(removed) == ["duel_wins", "hps_points", "rank"]
        async with engine.begin() as conn:
            left = await existing_columns(conn, "users")
            name = (await conn.execute(text("SELECT osu_username FROM users WHERE id = 1"))).scalar()
        assert not {"hps_points", "duel_wins", "rank"} & left and name == "legacy", "the rest of the record is untouched"
        assert json.loads(kept.read_text(encoding="utf-8")) == [
            {"id": 1, "chat_id": -100, "telegram_id": 1, "osu_username": "legacy", "hps_points": 120, "rank": "Candidate", "duel_wins": 4}
        ]
        assert await run_drop_old_chat_data_migration(engine, kept_in=str(tmp_path / "again.json")) == []
    finally:
        await engine.dispose()


async def test_the_cleanup_runs_on_a_database_that_is_not_sqlite_too(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from db.migrations import drop_old_chat_data
    from db.migrations._utils import existing_columns

    engine = await _fresh_engine()
    monkeypatch.setattr(drop_old_chat_data, "KEPT_IN", str(tmp_path))
    try:
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE users ADD COLUMN render_fps INTEGER"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN duel_wins INTEGER DEFAULT 0"))
        elsewhere = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), begin=engine.begin)
        await run_all_migrations(elsewhere)
        async with engine.begin() as conn:
            left = await existing_columns(conn, "users")
        assert not {"render_fps", "duel_wins"} & left and "render_skin" in left
        assert len(list(tmp_path.iterdir())) == 1, "the values were written aside once"
    finally:
        await engine.dispose()
