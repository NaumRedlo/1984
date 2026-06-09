"""DM tenant-selection tests.

In a private chat the bot has no group context, so the user picks which group's
data to act on; the choice is stored in ``dm_active_tenant`` and resolved into an
"effective tenant". These tests cover the resolver logic in ``utils.tenant``:
enumeration of the user's groups, the stored-choice round-trip, the stale-choice
self-heal, and the group-vs-DM ``effective_tenant`` branch.

In-memory aiosqlite + real ORM, mirroring test_multitenant.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.user import User
from db.models.dm_active_tenant import DmActiveTenant  # noqa: F401 (create_all)
from utils.tenant import (
    clear_dm_tenant,
    effective_tenant,
    get_dm_tenant,
    set_dm_tenant,
    user_tenants,
)

CHAT_A = -100
CHAT_B = -200


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory):
    """tg=1 registered in CHAT_A then CHAT_B; tg=2 only in CHAT_A."""
    async with factory() as s:
        s.add_all([
            User(chat_id=CHAT_A, telegram_id=1, osu_username="alice", osu_user_id=1001),
            User(chat_id=CHAT_B, telegram_id=1, osu_username="alice", osu_user_id=1001),
            User(chat_id=CHAT_A, telegram_id=2, osu_username="bob", osu_user_id=1002),
        ])
        await s.commit()


def _msg(chat_type: str, chat_id: int, tg_id: int):
    """Minimal Message/Callback stand-in for effective_tenant (_chat_of falls
    back to ``.chat`` for non-aiogram objects)."""
    return SimpleNamespace(
        chat=SimpleNamespace(type=chat_type, id=chat_id),
        from_user=SimpleNamespace(id=tg_id),
    )


# ── user_tenants ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_tenants_lists_groups_most_recent_first(factory):
    await _seed(factory)
    async with factory() as s:
        assert await user_tenants(s, 1) == [CHAT_B, CHAT_A]  # newest row first
        assert await user_tenants(s, 2) == [CHAT_A]
        assert await user_tenants(s, 999) == []


# ── set / get / clear ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_get_clear_round_trip(factory):
    await _seed(factory)
    async with factory() as s:
        assert await get_dm_tenant(s, 1) is None
        await set_dm_tenant(s, 1, CHAT_B)
        assert await get_dm_tenant(s, 1) == CHAT_B
        # Upsert to a different group.
        await set_dm_tenant(s, 1, CHAT_A)
        assert await get_dm_tenant(s, 1) == CHAT_A
        await clear_dm_tenant(s, 1)
        assert await get_dm_tenant(s, 1) is None


@pytest.mark.asyncio
async def test_get_dm_tenant_self_heals_stale_choice(factory):
    await _seed(factory)
    # tg=2 is only in CHAT_A but somehow has a stored choice of CHAT_B.
    async with factory() as s:
        await set_dm_tenant(s, 2, CHAT_B)
    async with factory() as s:
        # The stale choice is dropped (user has no row in CHAT_B) → None…
        assert await get_dm_tenant(s, 2) is None
        # …and the row was deleted, not just ignored.
        row = (await s.execute(
            select(DmActiveTenant).where(DmActiveTenant.telegram_id == 2)
        )).scalar_one_or_none()
        assert row is None


# ── effective_tenant ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_effective_tenant_group_uses_chat_id(factory):
    await _seed(factory)
    async with factory() as s:
        ev = _msg("supergroup", CHAT_A, tg_id=1)
        assert await effective_tenant(ev, s) == CHAT_A


@pytest.mark.asyncio
async def test_effective_tenant_dm_uses_selection(factory):
    await _seed(factory)
    async with factory() as s:
        await set_dm_tenant(s, 1, CHAT_B)
    async with factory() as s:
        ev = _msg("private", 555, tg_id=1)  # private chat id is irrelevant
        assert await effective_tenant(ev, s) == CHAT_B


@pytest.mark.asyncio
async def test_effective_tenant_dm_without_selection_is_none(factory):
    await _seed(factory)
    async with factory() as s:
        ev = _msg("private", 555, tg_id=1)
        assert await effective_tenant(ev, s) is None
