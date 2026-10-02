from datetime import datetime, timedelta

import pytest_asyncio
from aiogram.types import Chat, Message, User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.middlewares import last_seen_middleware as middleware
from db.database import Base
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from utils.timeutils import utcnow

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'silent.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def test_a_title_earned_by_coming_back_is_unlocked_without_a_word_in_the_chat(factory, monkeypatch):
    monkeypatch.setattr(middleware, "AsyncSessionFactory", factory)
    spoken = []

    async def answer(self, *args, **kwargs):
        spoken.append((args, kwargs))

    monkeypatch.setattr(Message, "answer", answer)
    async with factory() as s:
        user = User(chat_id=-100, telegram_id=555, osu_username="returning", osu_user_id=4040, play_count=0,
                    last_seen_at=utcnow() - timedelta(days=200))
        s.add(user)
        await s.commit()
        pid = user.player_id
    event = Message(message_id=1, date=datetime.now(), chat=Chat(id=-100, type="supergroup"),
                    from_user=TelegramUser(id=555, is_bot=False, first_name="Back"))
    reached = []

    async def handler(seen, data):
        reached.append(seen)
        return "handled"

    assert await middleware.LastSeenMiddleware()(handler, event, {}) == "handled"
    assert reached == [event] and spoken == []
    async with factory() as s:
        row = (await s.execute(select(UserTitleProgress).where(
            UserTitleProgress.player_id == pid, UserTitleProgress.title_code == "comeback_180d"))).scalar_one()
    assert row.unlocked is True

def test_no_message_is_left_to_announce_a_new_title():
    from utils.i18n import _CATALOG

    assert "common.title_unlocked" not in _CATALOG and "rs.titles_unlocked" not in _CATALOG
    assert "common.kb.leaderboard" in _CATALOG
