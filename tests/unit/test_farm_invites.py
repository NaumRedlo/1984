import hashlib
import types

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base

from db.models import RenderWorkerToken
from services.render_farm import http as farm_http
from services.render_farm import invites

@pytest.fixture(autouse=True)
def fresh():
    invites._good.clear()
    invites._owners.clear()
    yield
    invites._good.clear()
    invites._owners.clear()

def test_a_code_is_shown_in_two_halves_and_read_back_whatever_the_case():
    assert invites.pretty("ABCD2345") == "ABCD-2345"
    assert invites.tidy("abcd-2345") == "ABCD2345"
    assert invites.tidy("O0I1-l") == ""

def test_what_is_stored_is_not_the_token():
    token = "a" * 64
    assert invites.digest(token) == hashlib.sha256(token.encode()).hexdigest()
    assert token not in invites.digest(token)

def test_a_token_is_unknown_until_it_is_issued():
    assert not invites.known("a" * 64)
    invites.remember("a" * 64)
    assert invites.known("a" * 64)
    invites.forget(invites.digest("a" * 64))
    assert not invites.known("a" * 64)

@pytest_asyncio.fixture
async def database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    import db.database

    monkeypatch.setattr(db.database, "AsyncSessionFactory", factory)
    yield factory
    await engine.dispose()

async def test_redeeming_a_code_writes_a_row_and_returns_a_token(database):
    from sqlalchemy import select

    invite = invites.Invite(telegram_id=7, name="Naum", expires_at=1e9)
    token = await invites.issue(invite, "drejk")

    assert len(token) == 64 and invites.known(token)
    async with database() as session:
        rows = (await session.execute(select(RenderWorkerToken))).scalars().all()
    assert len(rows) == 1
    assert rows[0].digest == invites.digest(token)
    assert rows[0].issued_to == 7 and rows[0].worker == "drejk"
    assert token not in (rows[0].digest, rows[0].issued_name or "")

async def test_two_machines_get_two_tokens(database):
    first = await invites.issue(invites.Invite(1, "a", 1e9), "one")
    second = await invites.issue(invites.Invite(2, "b", 1e9), "two")
    assert first != second
    assert invites.known(first) and invites.known(second)

async def test_a_restart_remembers_who_is_enrolled(database, monkeypatch):
    token = await invites.issue(invites.Invite(1, "a", 1e9), "one")
    invites._good.clear()
    assert not invites.known(token), "the fixture did not actually clear it"

    import db.database

    monkeypatch.setattr(db.database, "AsyncSessionFactory", database)
    assert await invites.load() == 1
    assert invites.known(token)

async def test_a_revoked_machine_is_not_read_back(database, monkeypatch):
    from datetime import datetime, timezone

    from sqlalchemy import select

    token = await invites.issue(invites.Invite(1, "a", 1e9), "one")
    async with database() as session:
        row = (await session.execute(select(RenderWorkerToken))).scalars().one()
        row.revoked_at = datetime.now(timezone.utc)
        session.add(row)
        await session.commit()

    invites._good.clear()
    import db.database

    monkeypatch.setattr(db.database, "AsyncSessionFactory", database)
    await invites.load()
    assert not invites.known(token)

async def test_a_database_it_cannot_read_does_not_stop_the_bot(monkeypatch):
    import db.database

    def broken():
        raise RuntimeError("no database today")

    monkeypatch.setattr(db.database, "AsyncSessionFactory", broken)
    assert await invites.load() == 0

def _asking(token: str):
    return types.SimpleNamespace(headers={"Authorization": f"Bearer {token}"})

def test_the_old_shared_token_still_opens_the_door(monkeypatch):
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "the-old-one")
    assert farm_http._authorised(_asking("the-old-one"))

def test_a_token_issued_for_one_machine_opens_it_too(monkeypatch):
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "the-old-one")
    invites.remember("mine")
    assert farm_http._authorised(_asking("mine"))

def test_anything_else_does_not(monkeypatch):
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "the-old-one")
    invites.remember("mine")
    assert not farm_http._authorised(_asking("not-mine"))
    assert not farm_http._authorised(types.SimpleNamespace(headers={}))
