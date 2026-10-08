import pytest_asyncio
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, text
from sqlalchemy.ext.asyncio import create_async_engine

import db.models
from db.database import Base
from db.migrations import run_all_migrations
from db.migrations._utils import add_column, existing_columns, table_exists


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield engine
    await engine.dispose()


async def test_a_column_is_added_once_with_the_type_the_database_understands(engine):
    before = MetaData()
    Table("things", before, Column("id", Integer, primary_key=True))
    after = MetaData()
    things = Table("things", after, Column("id", Integer, primary_key=True), Column("seen_at", DateTime), Column("note", String(40)))
    async with engine.begin() as conn:
        await conn.run_sync(before.create_all)
        assert await existing_columns(conn, "things") == {"id"}
        assert await add_column(conn, things.c.seen_at) is True
        assert await add_column(conn, things.c.note) is True
        assert await add_column(conn, things.c.seen_at) is False
        assert await existing_columns(conn, "things") == {"id", "seen_at", "note"}
        await conn.execute(text("INSERT INTO things (id, seen_at, note) VALUES (1, NULL, 'kept')"))
        assert (await conn.execute(text("SELECT note FROM things WHERE id = 1"))).scalar() == "kept"


async def test_a_table_that_is_not_there_has_no_columns_and_is_told_apart(engine):
    async with engine.begin() as conn:
        assert await table_exists(conn, "nowhere") is False
        assert await existing_columns(conn, "nowhere") == set()


async def test_the_sqlite_history_is_not_replayed_on_another_database(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_all_migrations(engine)
    async with engine.begin() as conn:
        marks = (await conn.execute(text("SELECT COUNT(*) FROM bot_settings"))).scalar()
    assert (marks > 0) is (engine.dialect.name == "sqlite")
