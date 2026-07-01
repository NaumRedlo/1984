"""Card-rendering language preference (utils/language.py): global per Telegram
identity, defaults to EN. In-memory aiosqlite, mirroring
test_render_skin_ownership.py's pattern for session-opening functions."""

import contextlib

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.user_language import UserLanguage  # noqa: F401

import utils.language as lang_mod


@pytest_asyncio.fixture
async def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def _get_db_session():
        async with factory() as session:
            yield session

    monkeypatch.setattr(lang_mod, "get_db_session", _get_db_session)
    yield factory
    await engine.dispose()


async def test_get_language_defaults_to_en(db):
    assert await lang_mod.get_language(12345) == "EN"


async def test_has_language_false_by_default(db):
    assert await lang_mod.has_language(12345) is False


async def test_set_then_get_language(db):
    await lang_mod.set_language(12345, "RU")
    assert await lang_mod.get_language(12345) == "RU"
    assert await lang_mod.has_language(12345) is True


async def test_set_language_upserts(db):
    await lang_mod.set_language(12345, "RU")
    await lang_mod.set_language(12345, "EN")
    assert await lang_mod.get_language(12345) == "EN"


async def test_set_language_lowercase_input_normalized(db):
    await lang_mod.set_language(12345, "ru")
    assert await lang_mod.get_language(12345) == "RU"


async def test_language_is_per_telegram_id_not_shared(db):
    await lang_mod.set_language(1, "RU")
    assert await lang_mod.get_language(2) == "EN"
