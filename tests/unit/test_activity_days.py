import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from bot.middlewares import last_seen_middleware as activity
from db.database import Base
from db.models.player import Player
from db.models.user import User
from utils.title_progress import touch_activity_day


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(activity, "AsyncSessionFactory", factory)
    activity._last_updated.clear()
    async with factory() as session:
        session.add(User(chat_id=-100, telegram_id=7, osu_user_id=18767984, osu_username="kazak1865",
                         active_day=date(2026, 10, 3), active_streak=34, active_streak_best=34))
        await session.commit()
    yield factory
    activity._last_updated.clear()
    await engine.dispose()


def message(when, chat_id=-100):
    return Message(message_id=10, date=when.replace(tzinfo=timezone.utc), chat=Chat(id=chat_id, type="supergroup"),
                   from_user=TelegramUser(id=7, is_bot=False, first_name="Player"), text="hello")


async def stored(factory):
    async with factory() as session:
        user = (await session.execute(select(User).where(User.chat_id == -100))).scalar_one()
        player = await session.get(Player, user.player_id)
        assert (player.active_day, player.active_streak, player.active_streak_best) == (
            user.active_day, user.active_streak, user.active_streak_best,
        )
        return user


async def test_midnight_does_not_lose_a_day_during_the_cooldown(factory):
    middleware, handler = activity.LastSeenMiddleware(), AsyncMock()
    await middleware(handler, message(datetime(2026, 10, 3, 23, 59)), {})
    await middleware(handler, message(datetime(2026, 10, 4, 0, 1)), {})
    user = await stored(factory)
    assert (user.active_day, user.active_streak, user.active_streak_best) == (date(2026, 10, 4), 35, 35)
    await middleware(handler, message(datetime(2026, 10, 5, 0, 1)), {})
    assert (await stored(factory)).active_streak == 36
    assert handler.await_count == 3


async def test_failed_commit_can_retry_immediately(factory, monkeypatch):
    class FailedSession:
        async def __aenter__(self):
            self.session = factory()
            self.session.commit = AsyncMock(side_effect=RuntimeError("write failed"))
            return self.session

        async def __aexit__(self, *args):
            await self.session.close()

    middleware, handler = activity.LastSeenMiddleware(), AsyncMock()
    event = message(datetime(2026, 10, 4, 12))
    monkeypatch.setattr(activity, "AsyncSessionFactory", FailedSession)
    await middleware(handler, event, {})
    assert (await stored(factory)).active_streak == 34
    monkeypatch.setattr(activity, "AsyncSessionFactory", factory)
    await middleware(handler, event, {})
    assert (await stored(factory)).active_streak == 35
    assert handler.await_count == 2


async def test_plain_group_messages_count_without_matching_a_command(factory):
    dispatcher, bot = Dispatcher(), Bot(token="123456:TEST")
    dispatcher.message.outer_middleware(activity.LastSeenMiddleware())
    try:
        await dispatcher.feed_update(bot, Update(update_id=1, message=message(datetime(2026, 10, 4, 12))))
        assert (await stored(factory)).active_streak == 35
    finally:
        await bot.session.close()


async def test_two_chats_increment_the_shared_streak_once(factory):
    async with factory() as session:
        session.add(User(chat_id=-200, telegram_id=7, osu_user_id=18767984, osu_username="kazak1865"))
        await session.commit()
    middleware, handler = activity.LastSeenMiddleware(), AsyncMock()
    await asyncio.gather(*(
        middleware(handler, message(datetime(2026, 10, 4, 12), chat_id), {}) for chat_id in (-100, -200)
    ))
    assert (await stored(factory)).active_streak == 35


async def test_delayed_messages_do_not_rewind_activity(factory):
    middleware, handler = activity.LastSeenMiddleware(), AsyncMock()
    now = datetime(2026, 10, 4, 12)
    await middleware(handler, message(now), {})
    await middleware(handler, message(datetime(2026, 10, 2, 12)), {})
    user = await stored(factory)
    assert (user.active_day, user.active_streak, user.last_seen_at) == (now.date(), 35, now)


async def test_callback_uses_click_time_instead_of_old_message_date(factory, monkeypatch):
    now = datetime(2026, 10, 4, 12)
    monkeypatch.setattr(activity, "utcnow", lambda: now)
    old = message(datetime(2026, 9, 1))
    callback = CallbackQuery(id="click", from_user=old.from_user, chat_instance="chat", message=old, data="test")
    await activity.LastSeenMiddleware()(AsyncMock(), callback, {})
    user = await stored(factory)
    assert (user.active_streak, user.last_seen_at) == (35, now)


def test_a_real_gap_resets_only_the_current_streak():
    user = SimpleNamespace(active_day=date(2026, 10, 1), active_streak=34, active_streak_best=34)
    touch_activity_day(user, date(2026, 10, 4))
    assert (user.active_day, user.active_streak, user.active_streak_best) == (date(2026, 10, 4), 1, 34)
