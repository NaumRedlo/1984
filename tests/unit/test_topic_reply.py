"""Forum-topic reply resolution.

Inside a Telegram forum topic every top-level message carries a
``reply_to_message`` that points at the topic-creation service message, whose
``from_user`` is whoever opened the topic. Bare commands (``pf`` / ``rs`` /
``duels``) used to read that as a real reply and resolve to the topic creator
instead of the sender — players in the duel topic kept getting the topic
owner's card. ``get_real_reply`` filters that auto-reply out.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from db.database import Base
from db.models.user import User
from utils.osu.resolve_user import get_real_reply, get_reply_target_user

CHAT = -1001


def _msg(*, thread_id=None, reply=None):
    return SimpleNamespace(
        message_thread_id=thread_id,
        reply_to_message=reply,
        from_user=SimpleNamespace(id=1, is_bot=False),
        chat=SimpleNamespace(id=CHAT),
    )


def _reply(*, message_id, from_id, is_bot=False, forum_topic_created=None):
    return SimpleNamespace(
        message_id=message_id,
        from_user=SimpleNamespace(id=from_id, is_bot=is_bot),
        forum_topic_created=forum_topic_created,
    )


# ── get_real_reply (pure) ────────────────────────────────────────────────────


def test_no_reply_returns_none():
    assert get_real_reply(_msg()) is None


def test_genuine_reply_is_returned():
    # Replying to msg #50 inside topic #10 — a real reply, not the root.
    reply = _reply(message_id=50, from_id=2)
    msg = _msg(thread_id=10, reply=reply)
    assert get_real_reply(msg) is reply


def test_topic_root_by_thread_id_is_ignored():
    # Telegram auto-attaches the topic root (id == thread_id) as the reply.
    reply = _reply(message_id=10, from_id=999)  # 999 = topic creator
    msg = _msg(thread_id=10, reply=reply)
    assert get_real_reply(msg) is None


def test_topic_root_by_service_marker_is_ignored():
    reply = _reply(message_id=10, from_id=999, forum_topic_created=SimpleNamespace(name="Duels"))
    msg = _msg(thread_id=10, reply=reply)
    assert get_real_reply(msg) is None


def test_reply_outside_topic_still_works():
    # General chat (no thread) — normal reply behaviour is untouched.
    reply = _reply(message_id=77, from_id=2)
    msg = _msg(thread_id=None, reply=reply)
    assert get_real_reply(msg) is reply


# ── get_reply_target_user (DB-backed) ────────────────────────────────────────


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory):
    async with factory() as s:
        s.add_all([
            User(chat_id=CHAT, telegram_id=1, osu_username="sender", osu_user_id=1001),
            User(chat_id=CHAT, telegram_id=999, osu_username="topic_owner", osu_user_id=1999),
            User(chat_id=CHAT, telegram_id=2, osu_username="other", osu_user_id=1002),
        ])
        await s.commit()


@pytest.mark.asyncio
async def test_topic_root_does_not_resolve_to_creator(factory):
    await _seed(factory)
    # Bare `duels` in the duel topic: reply auto-points at the topic root (#10,
    # owner tg=999). Must NOT resolve to the topic owner.
    reply = _reply(message_id=10, from_id=999)
    msg = _msg(thread_id=10, reply=reply)
    async with factory() as s:
        assert await get_reply_target_user(s, msg) is None


@pytest.mark.asyncio
async def test_genuine_reply_resolves_target(factory):
    await _seed(factory)
    reply = _reply(message_id=55, from_id=2)
    msg = _msg(thread_id=10, reply=reply)
    async with factory() as s:
        target = await get_reply_target_user(s, msg)
    assert target is not None and target.telegram_id == 2
