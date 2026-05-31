from typing import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from config.settings import DATABASE_URL


class Base(DeclarativeBase):
    pass


_is_sqlite = DATABASE_URL.startswith("sqlite")

if _is_sqlite:
    # SQLite serializes writes at the file level — multiple pooled connections
    # racing for the write lock surface as `database is locked`.  NullPool
    # opens a fresh connection per checkout, which plays nicely with aiosqlite.
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        poolclass=NullPool,
        # Driver-level busy timeout: wait for the write lock instead of
        # raising "database is locked" the instant it's contended.
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        # WAL lets readers and the single writer proceed concurrently, so a
        # bulk import racing the bot's other tasks stops surfacing as
        # "database is locked" (set once, persists on the DB file).
        # busy_timeout backs up connect_args at the SQL level; synchronous=
        # NORMAL is the safe, faster pairing with WAL.
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()
else:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=60,
    )


AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)

@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def close_engine() -> None:
    await engine.dispose()


__all__ = [
    "Base",
    "engine",
    "AsyncSessionFactory",
    "get_db_session",
    "close_engine",
]
