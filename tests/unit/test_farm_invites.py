"""Letting a machine onto the farm with a code instead of a pasted secret.

There was one token for everybody, copied out of a chat by hand, and both
things wrong with that happened. A sixty-four character string copied slightly
wrong looks exactly like one copied right — two people ran for days against a
token that differed from the server's. And because everyone held the same
string, removing one person meant changing it for all of them.

So the tests here are about the two properties that replace it: a code is worth
one machine and ten minutes, and a token is worth exactly the machine it was
issued to. Plus the one thing that must not have broken — the old shared token
still works, because a deploy that quietly stops five people's computers is a
deploy that costs an evening.
"""

import hashlib
import time
import types

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
# Imported for its side effect as much as for the name: a model registers
# itself on `Base.metadata` when its module is imported, and the fixture below
# creates tables from that. Imported inside the tests instead, the table exists
# or not depending on which test ran first.
from db.models import RenderWorkerToken
from services.render_farm import http as farm_http
from services.render_farm import invites
from services.render_farm.queue import RenderQueue


@pytest.fixture(autouse=True)
def fresh():
    """These are module-level on purpose — one farm per process — so each test
    starts from an empty one rather than from whatever the last one left."""
    invites._codes.clear()
    invites._tries.clear()
    invites._good.clear()
    yield
    invites._codes.clear()
    invites._tries.clear()
    invites._good.clear()


# ── the code itself ─────────────────────────────────────────────────────────


def test_a_code_has_nothing_in_it_that_can_be_misread():
    """It is read off one screen and typed into another, often from a phone.
    `O` and `0`, `I` and `1`, `L` and `1` are the same character to somebody
    doing that, and a code that cannot be typed is worse than a long one."""
    code = invites.offer(1)
    assert len(code) == invites.CODE_LENGTH
    assert set(code) <= set(invites.ALPHABET)
    assert not set("OIL01U") & set(invites.ALPHABET)


def test_it_is_shown_in_two_halves():
    """Four and four is what somebody can hold in their head between one
    window and the next."""
    assert invites.pretty("ABCDEFGH") == "ABCD-EFGH"


def test_the_dash_and_the_case_are_not_part_of_the_code():
    """People type in lower case and they type the dash they were shown.
    Neither is a wrong code."""
    code = invites.offer(1)
    assert invites.redeem(invites.pretty(code).lower()) is not None


def test_a_code_is_worth_one_machine():
    """A code that stays good is a code somebody can scroll back to."""
    code = invites.offer(7, "Naum")
    assert invites.redeem(code).telegram_id == 7
    assert invites.redeem(code) is None


def test_a_code_that_sat_too_long_is_gone(monkeypatch):
    code = invites.offer(7)
    # The real one, captured before it is replaced. A lambda that calls
    # `time.monotonic()` after patching `time.monotonic` calls itself, and the
    # first version of this test hung rather than failing.
    now = time.monotonic()
    monkeypatch.setattr(invites.time, "monotonic",
                        lambda: now + invites.GOOD_FOR + 1)
    assert invites.redeem(code) is None


def test_wrong_used_and_expired_all_answer_the_same_way():
    """Telling them apart tells somebody guessing which half of the guess was
    right, and there is nothing a person redeeming a code does with the
    difference."""
    code = invites.offer(7)
    invites.redeem(code)
    assert invites.redeem(code) is None
    assert invites.redeem("22222222") is None


def test_a_script_hammering_this_is_refused_rather_than_merely_futile():
    """Thirty to the eighth over ten minutes is not a guessable space. This is
    here so that somebody trying is visible in a log rather than invisible in
    an access pattern."""
    good = invites.offer(7)
    for _ in range(invites.MOST_TRIES):
        invites.redeem("22222222")
    assert invites.redeem(good) is None, "the real code went through the wall"


# ── the token ───────────────────────────────────────────────────────────────


def test_what_is_stored_is_not_the_token():
    """A copy of that table must not be a working key for every machine on the
    farm."""
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
    # `invites` imports this at call time, so replacing the module attribute
    # is enough and nothing has to be threaded through the signatures.
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
    """Which is the whole point of the change: one can be taken away without
    touching the other."""
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
    """The farm is not worth a dead bot. The shared token still works and
    enrolled machines are refused until the next start."""
    import db.database

    def broken():
        raise RuntimeError("no database today")

    monkeypatch.setattr(db.database, "AsyncSessionFactory", broken)
    assert await invites.load() == 0


# ── the door ────────────────────────────────────────────────────────────────


def _asking(token: str):
    return types.SimpleNamespace(headers={"Authorization": f"Bearer {token}"})


def test_the_old_shared_token_still_opens_the_door(monkeypatch):
    """Machines set up before codes existed go on working. A deploy that
    quietly stops five people's computers is a deploy that costs an evening."""
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


@pytest_asyncio.fixture
async def farm(monkeypatch, database):
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "the-old-one")
    app = web.Application()
    app.add_routes(farm_http.make_routes(RenderQueue()))
    served = TestClient(TestServer(app))
    await served.start_server()
    yield served
    await served.close()


async def test_a_code_becomes_a_working_token_over_http(farm, monkeypatch):
    """End to end, which is the only way to know the two halves meet: the code
    goes in with no authorisation at all, and what comes back opens a door that
    was shut a moment earlier."""
    async def ours(*_args, **_kw):
        return "dossier 0.1.0 (abc1234)"

    monkeypatch.setattr(farm_http.engine_build, "local", ours)

    code = invites.offer(7, "Naum")
    reply = await farm.post("/render/join", json={"code": invites.pretty(code),
                                                  "name": "drejk"})
    assert reply.status == 200
    token = (await reply.json())["token"]

    said = await farm.get(
        "/render/hello",
        headers={"Authorization": f"Bearer {token}", "X-Render-Worker": "drejk"},
    )
    assert said.status == 200


async def test_a_wrong_code_gets_nothing_and_says_little(farm):
    reply = await farm.post("/render/join", json={"code": "22222222"})
    assert reply.status == 403
    assert "token" not in await reply.text()


async def test_the_door_that_hands_out_tokens_needs_none_itself(farm):
    """It is where a token comes from, so requiring one would be a circle. The
    code is the authorisation."""
    code = invites.offer(7)
    reply = await farm.post("/render/join", json={"code": code})
    assert reply.status == 200


async def test_nonsense_is_a_bad_request_rather_than_a_traceback(farm):
    reply = await farm.post("/render/join", data=b"not json at all")
    assert reply.status == 400


async def test_the_real_client_swaps_a_code_for_a_key_and_then_gets_in(
    farm, monkeypatch
):
    """Both halves, over a real socket. The bot hands out the code and the
    client out of the `dossier` package — the same program a friend downloads —
    redeems it and then opens a door that was shut a moment before.

    The two live in different repositories and can be changed weeks apart by
    somebody with only one of them open, which is exactly why this is here
    rather than two tests that each trust the other to have kept its half.
    """
    from dossier import console

    async def ours(*_args, **_kw):
        return "dossier 0.1.0 (abc1234)"

    monkeypatch.setattr(farm_http.engine_build, "local", ours)
    base = str(farm.make_url(""))

    code = invites.pretty(invites.offer(7, "Naum"))
    token, why = await console.redeem(base, code, "drejk")
    assert token, why

    said = await farm.get(
        "/render/hello",
        headers={"Authorization": f"Bearer {token}", "X-Render-Worker": "drejk"},
    )
    assert said.status == 200


async def test_the_client_is_told_plainly_when_a_code_is_no_good(farm):
    """"Wrong, used or expired" is one answer from the bot on purpose, and the
    client has to turn it into a sentence rather than a status code."""
    from dossier import console

    token, why = await console.redeem(str(farm.make_url("")), "2222-2222", "drejk")
    assert not token
    assert "код не подошёл" in why, why
