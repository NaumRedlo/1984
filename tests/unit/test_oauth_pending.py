import contextlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.oauth_pending import OAuthPending
from services.oauth import server


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def session():
        async with made() as s:
            yield s

    monkeypatch.setattr(server, "get_db_session", session)
    yield made
    await engine.dispose()


def _state(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


async def test_a_sent_link_is_kept_in_the_database_with_its_message(factory):
    state = _state(await server.generate_oauth_url(42))
    await server.track_link_message(42, -100, 7)

    entry = await server._take_state(state)
    assert (entry.telegram_id, entry.chat_id, entry.message_id) == (42, -100, 7)
    assert await server._take_state(state) is None, "a link is good for one use"


async def test_an_old_link_is_refused_and_swept(factory):
    async with factory() as session:
        session.add(OAuthPending(state="old", telegram_id=1,
                                 issued_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        await session.commit()
    await server.generate_oauth_url(2)
    assert await server._take_state("old") is None
    async with factory() as session:
        left = (await session.execute(select(OAuthPending.telegram_id))).scalars().all()
    assert left == [2]


async def test_an_unknown_state_is_refused(factory):
    assert await server._take_state("made-up") is None
