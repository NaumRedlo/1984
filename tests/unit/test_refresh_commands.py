import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from bot.filters import TriggerArgs
from bot.handlers.profile import handlers as profile, update
from bot.handlers.profile.targets import Target
from db.database import Base
from db.models.user import User
from services.command_refresh import RefreshRequests


def message(chat_id=-100, user_id=7):
    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id), chat=SimpleNamespace(id=chat_id),
                           answer=AsyncMock(return_value=status), answer_photo=AsyncMock()), status


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'commands.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session():
        async with factory() as current:
            yield current

    monkeypatch.setattr(update, "get_db_session", session)
    monkeypatch.setattr(profile, "get_db_session", session)
    monkeypatch.setattr(update, "get_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(profile, "get_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(update, "refresh_requests", RefreshRequests())
    monkeypatch.setattr(profile, "refresh_requests", RefreshRequests())
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_two_players_updating_same_target_share_fetch_and_committed_baseline(database, monkeypatch):
    target = Target(123, "Player")
    monkeypatch.setattr(update, "resolve_target", AsyncMock(return_value=target))
    monkeypatch.setattr(update, "token_for", AsyncMock(return_value=None))
    started, release = asyncio.Event(), asyncio.Event()

    async def best(*args, **kwargs):
        started.set()
        await release.wait()
        return []

    client = SimpleNamespace(
        get_user_data=AsyncMock(return_value={"username": "Player", "playmode": "osu", "pp": 123,
                                             "accuracy": 98, "play_count": 456}),
        get_user_best_scores=AsyncMock(side_effect=best),
    )
    renderer = AsyncMock(side_effect=lambda data: BytesIO(b"png"))
    monkeypatch.setattr(update.card_renderer, "generate_update_card_async", renderer)
    a, _ = message()
    b, bstatus = message(user_id=8)
    args = TriggerArgs("upd", "Player", "upd Player")
    first = asyncio.create_task(update.cmd_update(a, args, client))
    await started.wait()
    second = asyncio.create_task(update.cmd_update(b, args, client))
    for _ in range(5):
        await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    client.get_user_best_scores.assert_awaited_once()
    a.answer_photo.assert_awaited_once()
    b.answer_photo.assert_awaited_once()
    assert renderer.await_count == 2
    assert all(call.args[0]["first"] and call.args[0]["now"]["pp"] == 123 for call in renderer.call_args_list)
    async with database() as session:
        snapshot = await update.tracking.load(session, 123, 0)
        assert snapshot is not None and snapshot.pp == 123
    b.answer_photo.reset_mock()
    await update.cmd_update(b, args, client)
    b.answer_photo.assert_not_awaited()
    assert "недавно обновлён" in bstatus.edit_text.call_args.args[0]
    client.get_user_best_scores.assert_awaited_once()


async def test_profile_refresh_uses_player_scope_across_chats_and_survives_cancelled_waiter(database, monkeypatch):
    async with database() as session:
        a = User(chat_id=-100, telegram_id=7, osu_user_id=123, osu_username="Player")
        b = User(chat_id=-200, telegram_id=7, osu_user_id=123, osu_username="Player")
        session.add_all([a, b])
        await session.commit()
    users = {-100: a, -200: b}

    async def registered(session, message, **kwargs):
        return users[message.chat.id]

    monkeypatch.setattr(profile, "require_registered_user", registered)
    started, release = asyncio.Event(), asyncio.Event()

    async def refresh(subject, session, client, **kwargs):
        started.set()
        await release.wait()
        subject.player_pp = 555
        await session.flush()
        return True

    refreshing = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(profile, "refresh_user", refreshing)
    first_message, _ = message()
    other_message, other_status = message(-200)
    first = asyncio.create_task(profile.refresh_profile(first_message, object()))
    await started.wait()
    await profile.refresh_profile(other_message, object())
    assert "ещё выполняется" in other_status.edit_text.call_args.args[0]
    first.cancel()
    try:
        await first
    except asyncio.CancelledError:
        pass
    release.set()
    while profile.refresh_requests._active:
        await asyncio.sleep(0)
    async with database() as session:
        kept = await session.get(User, a.id)
        assert kept.player_pp == 555
    await profile.refresh_profile(other_message, object())
    assert "недавно обновлён" in other_status.edit_text.call_args.args[0]
    refreshing.assert_awaited_once()
