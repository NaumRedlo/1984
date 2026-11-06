import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.database
import db.models
from db.database import Base
from services.render_farm import trust


@pytest_asyncio.fixture
async def database(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trust.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db.database, "AsyncSessionFactory", sessions)
    monkeypatch.setattr(trust, "_owners", set())
    monkeypatch.setattr(trust, "ADMIN_IDS", [1])
    yield sessions
    await engine.dispose()


async def test_trust_is_granted_kept_across_a_restart_and_taken_back(database):
    assert db.models
    assert trust.trusted(1) and not trust.trusted(0)
    assert not trust.trusted(77)
    assert await trust.grant(77) and await trust.grant(78)
    assert not await trust.grant(77)
    assert trust.trusted(77) and trust.listed() == [77, 78]
    trust._owners.clear()
    assert not trust.trusted(77)
    assert await trust.load() == 2
    assert trust.trusted(77) and trust.trusted(78)
    assert await trust.revoke(77)
    assert not await trust.revoke(77)
    assert await trust.load() == 1
    assert not trust.trusted(77) and trust.trusted(78)
