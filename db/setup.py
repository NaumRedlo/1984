from sqlalchemy import text

from db.database import Base
from db.migrations import run_all_migrations

LOCK = 19840004


async def prepare(engine) -> None:
    held = None
    if engine.dialect.name == "postgresql":
        held = await engine.connect()
        await held.execute(text("select pg_advisory_lock(:key)"), {"key": LOCK})
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await run_all_migrations(engine)
    finally:
        if held is not None:
            try:
                await held.execute(text("select pg_advisory_unlock(:key)"), {"key": LOCK})
            finally:
                await held.close()
