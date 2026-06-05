"""Unit tests for tasks.bounty_weekly expiry reminders.

Covers the two fixes:
  - reminders are batched into ONE digest instead of one message per bounty
    (auto-bounties share the weekly deadline, so the old code fired a burst
    of dozens at once);
  - the reminder chat prefers bounty_notify_chat_id (/setbountychat),
    falling back to weekly_chat_id.

Pattern mirrors test_weekly_generator_integration.py: in-memory aiosqlite
with Base.metadata.create_all, real ORM. send_expiry_reminders reaches for
get_db_session() itself, so we patch that to hand it our test session.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import tasks.bounty_weekly as bw
from db.database import Base
from db.models.bounty import Bounty  # noqa: F401  (registers table)
from db.models.bot_settings import BotSettings
from db.models.user import User


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _patch_db(factory):
    @contextlib.asynccontextmanager
    async def _fake_get_db_session():
        async with factory() as s:
            yield s
    return patch.object(bw, "get_db_session", _fake_get_db_session)


class _FakeBot:
    def __init__(self):
        self.messages: list[tuple] = []  # (chat_id, text, thread_id)

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs.get("message_thread_id")))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_bounties(factory, n, *, hours=12, reminded=False,
                         status="active", id_prefix="auto"):
    now = _utcnow()
    async with factory() as s:
        for i in range(n):
            s.add(Bounty(
                bounty_id=f"{id_prefix}-{i}",
                title=f"Map {i}",
                beatmap_id=1000 + i,
                beatmap_title=f"artist - song {i} [diff]",
                star_rating=4.2,
                drain_time=120,
                created_by=0,
                source="auto",
                tier="C",
                status=status,
                deadline=now + timedelta(hours=hours),
                reminder_sent=reminded,
            ))
        await s.commit()


async def _count_reminded(factory) -> int:
    async with factory() as s:
        rows = (await s.execute(
            select(Bounty).where(Bounty.reminder_sent.is_(True))
        )).scalars().all()
        return len(rows)


@pytest.mark.asyncio
async def test_batches_into_single_message(factory):
    await _seed_bounties(factory, 36, hours=12)
    bot = _FakeBot()
    with _patch_db(factory):
        n = await bw.send_expiry_reminders(bot, chat_id=-100)
    assert n == 36
    # The whole point: ONE message, not 36.
    assert len(bot.messages) == 1
    assert bot.messages[0][0] == -100
    assert "36" in bot.messages[0][1]
    # All 36 are stamped so the next tick is silent.
    assert await _count_reminded(factory) == 36


@pytest.mark.asyncio
async def test_second_run_is_silent(factory):
    await _seed_bounties(factory, 5, hours=12)
    bot = _FakeBot()
    with _patch_db(factory):
        first = await bw.send_expiry_reminders(bot, chat_id=1)
        second = await bw.send_expiry_reminders(bot, chat_id=1)
    assert first == 5
    assert second == 0
    assert len(bot.messages) == 1


@pytest.mark.asyncio
async def test_excludes_far_reminded_and_inactive(factory):
    await _seed_bounties(factory, 3, hours=12, id_prefix="due")          # in window
    await _seed_bounties(factory, 4, hours=48, id_prefix="far")          # > 24h away
    await _seed_bounties(factory, 2, hours=12, reminded=True, id_prefix="done")
    await _seed_bounties(factory, 2, hours=12, status="expired", id_prefix="dead")
    bot = _FakeBot()
    with _patch_db(factory):
        n = await bw.send_expiry_reminders(bot, chat_id=1)
    assert n == 3
    assert len(bot.messages) == 1


@pytest.mark.asyncio
async def test_no_bounties_sends_nothing(factory):
    bot = _FakeBot()
    with _patch_db(factory):
        n = await bw.send_expiry_reminders(bot, chat_id=1)
    assert n == 0
    assert bot.messages == []


# ── chat routing ────────────────────────────────────────────────────────────


async def _set(factory, key, value):
    async with factory() as s:
        s.add(BotSettings(key=key, value=str(value)))
        await s.commit()


@pytest.mark.asyncio
async def test_reminder_chat_prefers_bounty_channel(factory):
    await _set(factory, "weekly_chat_id", -111)
    await _set(factory, "bounty_notify_chat_id", -222)
    with _patch_db(factory):
        assert await bw._get_reminder_target() == (-222, None)


@pytest.mark.asyncio
async def test_reminder_chat_falls_back_to_weekly(factory):
    await _set(factory, "weekly_chat_id", -111)
    with _patch_db(factory):
        assert await bw._get_reminder_target() == (-111, None)


@pytest.mark.asyncio
async def test_reminder_chat_none_when_unset(factory):
    with _patch_db(factory):
        assert await bw._get_reminder_target() == (None, None)


@pytest.mark.asyncio
async def test_reminder_target_includes_bounty_thread(factory):
    # Bounty chat set → use its chat+topic, ignoring the weekly fallback.
    await _set(factory, "bounty_notify_chat_id", -222)
    await _set(factory, "bounty_notify_thread_id", 7)
    await _set(factory, "weekly_chat_id", -111)
    await _set(factory, "weekly_thread_id", 9)
    with _patch_db(factory):
        assert await bw._get_reminder_target() == (-222, 7)


@pytest.mark.asyncio
async def test_reminder_target_fallback_uses_weekly_thread(factory):
    await _set(factory, "weekly_chat_id", -111)
    await _set(factory, "weekly_thread_id", 9)
    with _patch_db(factory):
        assert await bw._get_reminder_target() == (-111, 9)


@pytest.mark.asyncio
async def test_reminder_digest_forwarded_to_thread(factory):
    await _seed_bounties(factory, 2, hours=12)
    bot = _FakeBot()
    with _patch_db(factory):
        n = await bw.send_expiry_reminders(bot, chat_id=-100, thread_id=42)
    assert n == 2
    assert len(bot.messages) == 1
    assert bot.messages[0][2] == 42  # message_thread_id forwarded


# ── multi-tenant fan-out ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_target_fanout_sends_to_each_and_marks_once(factory):
    """Global bounty content → the SAME digest goes to every tenant group, but
    `reminder_sent` is stamped only once so re-runs stay quiet."""
    await _seed_bounties(factory, 4, hours=12)
    bot = _FakeBot()
    with _patch_db(factory):
        n = await bw.send_expiry_reminders_multi(bot, [(-100, None), (-200, 9)])
    assert n == 4
    # One message per target, identical body.
    assert [m[0] for m in bot.messages] == [-100, -200]
    assert bot.messages[0][1] == bot.messages[1][1]
    assert bot.messages[1][2] == 9  # per-chat thread honored
    assert await _count_reminded(factory) == 4

    # Second run: nothing left to remind, no messages.
    bot2 = _FakeBot()
    with _patch_db(factory):
        n2 = await bw.send_expiry_reminders_multi(bot2, [(-100, None), (-200, 9)])
    assert n2 == 0
    assert bot2.messages == []


@pytest.mark.asyncio
async def test_fanout_targets_cover_active_tenants(factory):
    """_fanout_targets = every active tenant group, with the configured forum
    topic applied only to its own chat; a configured chat with no users is still
    included."""
    async with factory() as s:
        s.add_all([
            User(chat_id=-100, telegram_id=1, osu_username="a", osu_user_id=1),
            User(chat_id=-200, telegram_id=2, osu_username="b", osu_user_id=2),
        ])
        await s.commit()
    with _patch_db(factory):
        targets = await bw._fanout_targets(cfg_chat=-100, cfg_thread=7)
    targets_map = dict(targets)
    assert set(targets_map) == {-100, -200}
    assert targets_map[-100] == 7      # configured topic applied to its own chat
    assert targets_map[-200] is None   # other tenants → General topic


@pytest.mark.asyncio
async def test_fanout_includes_configured_chat_without_users(factory):
    async with factory() as s:
        s.add(User(chat_id=-100, telegram_id=1, osu_username="a", osu_user_id=1))
        await s.commit()
    with _patch_db(factory):
        targets = await bw._fanout_targets(cfg_chat=-555, cfg_thread=3)
    targets_map = dict(targets)
    assert set(targets_map) == {-100, -555}
    assert targets_map[-555] == 3
