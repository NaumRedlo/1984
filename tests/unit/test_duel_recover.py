"""Characterization tests for services.duel.duel_recover.recover_active_duels.

Pins the restart-recovery dispatch — the stability-critical path that only ran
in production before:
  * accepted / round_active duels are resumed (round engine relaunched);
  * pending duels past their accept deadline are flipped to 'expired' and the
    stranded challenge message is edited;
  * pending duels still in-window (or with no deadline) get their expiry timer
    re-armed;
  * completed / cancelled duels are left untouched.

In-memory aiosqlite (real ORM) + monkeypatched launch / _expire_duel /
safe_edit_text so no engine task, timer, or Telegram call actually fires.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.duel.duel_recover as dr
import services.duel.round_engine as round_engine
import services.duel.duel_manager as duel_manager
import utils.telegram_safe as telegram_safe
from db.database import Base
from db.models.duel import Duel
from db.models.user import User  # noqa: F401  (registers the users table for the FK)


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
    return _fake_get_db_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed(factory, rows: list[dict]) -> None:
    async with factory() as s:
        for r in rows:
            s.add(Duel(**r))
        await s.commit()


@pytest.mark.asyncio
async def test_recover_dispatches_each_state(factory, monkeypatch):
    now = _utcnow()
    past = now - timedelta(minutes=5)
    future = now + timedelta(minutes=5)
    base = dict(player1_user_id=1, player2_user_id=2)
    await _seed(factory, [
        {**base, "id": 1, "status": "round_active"},
        {**base, "id": 2, "status": "accepted"},
        {**base, "id": 3, "status": "pending", "expires_at": past,
         "chat_id": -100, "message_id": 55},
        {**base, "id": 4, "status": "pending", "expires_at": future},
        {**base, "id": 5, "status": "completed"},
        {**base, "id": 6, "status": "pending", "expires_at": None},
    ])

    launch = MagicMock()
    expire = AsyncMock()
    edit = AsyncMock()
    monkeypatch.setattr(dr, "get_db_session", _patch_db(factory))
    monkeypatch.setattr(round_engine, "launch", launch)
    monkeypatch.setattr(duel_manager, "_expire_duel", expire)
    monkeypatch.setattr(telegram_safe, "safe_edit_text", edit)

    bot, osu = object(), object()
    await dr.recover_active_duels(bot, osu)
    await asyncio.sleep(0)  # let the create_task'd expiry timers run to completion

    # accepted + round_active are resumed via the engine, with bot/osu forwarded.
    resumed = {c.args[2] for c in launch.call_args_list}
    assert resumed == {1, 2}
    for c in launch.call_args_list:
        assert c.args[0] is bot and c.args[1] is osu

    # in-window (4) and deadline-less (6) pendings get their expiry re-armed.
    rearmed = {c.args[1] for c in expire.call_args_list}
    assert rearmed == {4, 6}

    # past-deadline pending (3) is flipped to expired; completed (5) is untouched.
    async with factory() as s:
        d3 = (await s.execute(select(Duel.status).where(Duel.id == 3))).scalar_one()
        d5 = (await s.execute(select(Duel.status).where(Duel.id == 5))).scalar_one()
    assert d3 == "expired"
    assert d5 == "completed"

    # exactly the one stranded challenge with a chat+message gets its edit.
    assert edit.call_count == 1


@pytest.mark.asyncio
async def test_recover_noop_when_nothing_to_resume(factory, monkeypatch):
    await _seed(factory, [
        dict(id=1, player1_user_id=1, player2_user_id=2, status="completed"),
        dict(id=2, player1_user_id=1, player2_user_id=2, status="cancelled"),
    ])
    launch = MagicMock()
    expire = AsyncMock()
    monkeypatch.setattr(dr, "get_db_session", _patch_db(factory))
    monkeypatch.setattr(round_engine, "launch", launch)
    monkeypatch.setattr(duel_manager, "_expire_duel", expire)

    await dr.recover_active_duels(object(), object())
    await asyncio.sleep(0)

    launch.assert_not_called()
    expire.assert_not_called()
