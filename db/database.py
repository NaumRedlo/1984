from typing import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

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

def _wall_clock(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if isinstance(value, dict):
        return {key: _wall_clock(inner) for key, inner in value.items()}
    if isinstance(value, tuple):
        return tuple(_wall_clock(inner) for inner in value)
    if isinstance(value, list):
        return [_wall_clock(inner) for inner in value]
    return value

def attach_postgres(target) -> None:
    @event.listens_for(target.sync_engine, "before_cursor_execute", retval=True)
    def _drop_time_zones(_conn, _cursor, statement, parameters, _context, _executemany):
        return statement, _wall_clock(parameters)

_is_sqlite = DATABASE_URL.startswith("sqlite")

if _is_sqlite:

    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        poolclass=NullPool,

        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):

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
    attach_postgres(engine)

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
    "attach_postgres",
    "engine",
    "AsyncSessionFactory",
    "get_db_session",
    "close_engine",
]
