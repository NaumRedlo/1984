import asyncio
import os
import uuid

import pytest

POSTGRES = os.getenv("TEST_DATABASE_URL", "")
_schemas: dict[str, str] = {}

if POSTGRES:
    import sqlalchemy.ext.asyncio as _asyncio_sa
    from sqlalchemy import event, text
    from sqlalchemy.pool import NullPool

    from db.database import attach_postgres

    _real_engine = _asyncio_sa.create_async_engine

    def _postgres_engine(url, *args, **kwargs):
        name = str(url)
        if not name.startswith("sqlite"):
            return _real_engine(url, *args, **kwargs)
        shared = None if name.endswith(":memory:") else name
        schema = _schemas.get(shared) if shared else None
        if schema is None:
            schema = f"t_{uuid.uuid4().hex}"
            _schemas[shared or schema] = schema
        engine = _real_engine(POSTGRES, poolclass=NullPool)
        attach_postgres(engine)

        @event.listens_for(engine.sync_engine, "connect")
        def _own_schema(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                cursor.execute(f'SET search_path TO "{schema}"')
            finally:
                cursor.close()

        return engine

    _asyncio_sa.create_async_engine = _postgres_engine

    def pytest_collection_modifyitems(items):
        skip = pytest.mark.skip(reason="SQLite only")
        for item in items:
            if item.get_closest_marker("sqlite_only"):
                item.add_marker(skip)

    def pytest_sessionfinish(session, exitstatus):
        async def _drop():
            engine = _real_engine(POSTGRES, poolclass=NullPool)
            async with engine.connect() as conn:
                conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                for schema in set(_schemas.values()):
                    await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

        asyncio.run(_drop())


@pytest.fixture(autouse=True)
def fresh_render_memories():
    from services.render_farm import http

    http.forget_memories()
    yield
    http.forget_memories()
