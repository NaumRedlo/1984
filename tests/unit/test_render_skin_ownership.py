"""Skin ownership tracking (bot/handlers/profile/render.py): only the uploader
of a skin may rename/delete it later; the general picker stays select-only for
everyone. In-memory aiosqlite + real ORM, mirroring test_multitenant.py."""

import contextlib
import json

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.bot_settings import BotSettings  # noqa: F401
from db.models.user import User
from db.models.render_settings import UserRenderSettings

import bot.handlers.profile.render as r


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

    monkeypatch.setattr(r, "get_db_session", _get_db_session)
    yield factory
    await engine.dispose()


async def test_get_render_skins_empty(db):
    assert await r.get_render_skins() == []


async def test_add_and_list_skins_with_owner(db):
    await r._add_render_skin("Cool Skin", owner_tg_id=111)
    assert await r.get_render_skins() == [{"name": "Cool Skin", "owner": 111}]


async def test_legacy_string_entries_have_no_owner(db, monkeypatch):
    # Data written before ownership tracking existed was a plain list of names.
    async with db() as session:
        session.add(BotSettings(key=r._SKINS_KEY, value=json.dumps(["OldSkin"])))
        await session.commit()
    assert await r.get_render_skins() == [{"name": "OldSkin", "owner": None}]


async def test_my_render_skins_filters_by_owner(db):
    await r._add_render_skin("Mine", owner_tg_id=1)
    await r._add_render_skin("Theirs", owner_tg_id=2)
    await r._add_render_skin("Unowned", owner_tg_id=None)
    mine = await r.get_my_render_skins(1)
    assert [e["name"] for e in mine] == ["Mine"]


async def test_reupload_claims_previously_unowned_entry(db):
    async with db() as session:
        session.add(BotSettings(key=r._SKINS_KEY, value=json.dumps(["Legacy"])))
        await session.commit()
    await r._add_render_skin("Legacy", owner_tg_id=42)
    assert await r.get_render_skins() == [{"name": "Legacy", "owner": 42}]


async def test_reupload_does_not_steal_existing_owner(db):
    await r._add_render_skin("Taken", owner_tg_id=1)
    await r._add_render_skin("Taken", owner_tg_id=2)  # someone else re-uploads the same name
    assert await r.get_render_skins() == [{"name": "Taken", "owner": 1}]


async def test_remove_render_skin(db):
    await r._add_render_skin("A", owner_tg_id=1)
    await r._add_render_skin("B", owner_tg_id=1)
    await r._remove_render_skin("A")
    assert [e["name"] for e in await r.get_render_skins()] == ["B"]


async def test_rename_render_skin_entry_keeps_owner(db):
    await r._add_render_skin("Old", owner_tg_id=1)
    await r._rename_render_skin_entry("Old", "New")
    assert await r.get_render_skins() == [{"name": "New", "owner": 1}]


async def test_reassign_users_off_skin(db):
    async with db() as session:
        u = User(chat_id=-1, telegram_id=1, osu_username="a", osu_user_id=1)
        session.add(u)
        await session.flush()
        session.add(UserRenderSettings(user_id=u.id, skin="OldSkin"))
        await session.commit()
        uid = u.id

    await r._reassign_users_off_skin("OldSkin", "default")

    async with db() as session:
        s = (await session.execute(
            select(UserRenderSettings).where(UserRenderSettings.user_id == uid)
        )).scalar_one()
        assert s.skin == "default"


async def test_reassign_users_off_skin_leaves_other_skins_alone(db):
    async with db() as session:
        u = User(chat_id=-1, telegram_id=1, osu_username="a", osu_user_id=1)
        session.add(u)
        await session.flush()
        session.add(UserRenderSettings(user_id=u.id, skin="Untouched"))
        await session.commit()
        uid = u.id

    await r._reassign_users_off_skin("SomeOtherSkin", "default")

    async with db() as session:
        s = (await session.execute(
            select(UserRenderSettings).where(UserRenderSettings.user_id == uid)
        )).scalar_one()
        assert s.skin == "Untouched"


async def test_do_delete_skin_cleans_up_list_and_users(db, monkeypatch):
    async def fake_delete_remote(name):
        assert name == "ToDelete"

    monkeypatch.setattr(r.render_client, "delete_skin_remote", fake_delete_remote)
    await r._add_render_skin("ToDelete", owner_tg_id=1)

    async with db() as session:
        u = User(chat_id=-1, telegram_id=1, osu_username="a", osu_user_id=1)
        session.add(u)
        await session.flush()
        session.add(UserRenderSettings(user_id=u.id, skin="ToDelete"))
        await session.commit()
        uid = u.id

    await r.do_delete_skin("ToDelete")

    assert await r.get_render_skins() == []
    async with db() as session:
        s = (await session.execute(
            select(UserRenderSettings).where(UserRenderSettings.user_id == uid)
        )).scalar_one()
        assert s.skin == "default"


async def test_do_rename_skin_updates_list_and_users(db, monkeypatch):
    async def fake_rename_remote(name, new_name):
        assert (name, new_name) == ("Old", "Shiny New")
        return "Shiny New"  # worker-sanitized name

    monkeypatch.setattr(r.render_client, "rename_skin_remote", fake_rename_remote)
    await r._add_render_skin("Old", owner_tg_id=1)

    async with db() as session:
        u = User(chat_id=-1, telegram_id=1, osu_username="a", osu_user_id=1)
        session.add(u)
        await session.flush()
        session.add(UserRenderSettings(user_id=u.id, skin="Old"))
        await session.commit()
        uid = u.id

    final_name = await r.do_rename_skin("Old", "Shiny New")

    assert final_name == "Shiny New"
    assert await r.get_render_skins() == [{"name": "Shiny New", "owner": 1}]
    async with db() as session:
        s = (await session.execute(
            select(UserRenderSettings).where(UserRenderSettings.user_id == uid)
        )).scalar_one()
        assert s.skin == "Shiny New"
