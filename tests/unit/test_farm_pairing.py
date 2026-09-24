import time
import types

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models import RenderWorkerToken
from services.render_farm import http as farm_http
from services.render_farm import invites, pairing

MAC = pairing.Machine("MacBook Pro Наума", "macOS on ARM", 10, "0.11.0")

@pytest.fixture(autouse=True)
def fresh():
    for book in (pairing._pending, pairing._starts, pairing._misses,
                 invites._good, invites._owners):
        book.clear()
    pairing.set_bot_username("")
    yield
    for book in (pairing._pending, pairing._starts, pairing._misses,
                 invites._good, invites._owners):
        book.clear()
    pairing.set_bot_username("")

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

def test_a_code_is_the_same_kind_the_bot_already_hands_out():
    code = pairing.start(MAC, "1.1.1.1")
    assert len(code) == invites.CODE_LENGTH
    assert set(code) <= set(invites.ALPHABET)
    assert invites.pretty(code) == f"{code[:4]}-{code[4:]}"

def test_the_link_opens_the_bot_with_the_code_already_typed():
    pairing.set_bot_username("@osu1984bot")
    assert pairing.link("K7QN-M4XZ") == "https://t.me/osu1984bot?start=pair-K7QNM4XZ"

def test_without_a_username_there_is_no_link_rather_than_a_wrong_one():
    assert pairing.link("K7QNM4XZ") == ""

def test_the_card_can_say_which_machine_this_is():
    code = pairing.start(MAC, "1.1.1.1")
    found = pairing.describe(invites.pretty(code).lower())
    assert found.machine == MAC
    assert found.linked_to is None

async def test_waiting_then_yes_then_the_token_once(database):
    code = pairing.start(MAC, "1.1.1.1")

    assert (await pairing.collect(code, "1.1.1.1")).status == pairing.WAITING
    assert (await pairing.collect(code, "1.1.1.1")).status == pairing.WAITING

    linked = pairing.approve(code, 7, "Naum")
    assert linked.linked_to == 7 and linked.machine == MAC

    got = await pairing.collect(code, "1.1.1.1")
    assert got.status == pairing.LINKED
    assert len(got.token) == 64 and invites.known(got.token)

    assert (await pairing.collect(code, "1.1.1.1")).status == pairing.GONE

async def test_the_token_is_written_for_the_person_who_said_yes(database):
    from sqlalchemy import select

    code = pairing.start(MAC, "1.1.1.1")
    pairing.approve(code, 7, "Naum")
    got = await pairing.collect(code, "1.1.1.1")

    async with database() as session:
        row = (await session.execute(select(RenderWorkerToken))).scalars().one()
    assert row.digest == invites.digest(got.token)
    assert row.issued_to == 7 and row.issued_name == "Naum"
    assert row.worker == MAC.name

async def test_nothing_is_written_until_the_application_collects(database):
    from sqlalchemy import select

    code = pairing.start(MAC, "1.1.1.1")
    pairing.approve(code, 7, "Naum")
    async with database() as session:
        assert (await session.execute(select(RenderWorkerToken))).scalars().all() == []

def test_yes_twice_is_one_yes():
    code = pairing.start(MAC, "1.1.1.1")
    assert pairing.approve(code, 7) is not None
    assert pairing.approve(code, 8) is None

async def test_no_ends_it(database):
    code = pairing.start(MAC, "1.1.1.1")
    assert pairing.decline(code)
    assert (await pairing.collect(code, "1.1.1.1")).status == pairing.GONE
    assert not pairing.decline(code)

async def test_a_code_that_sat_too_long_is_gone(monkeypatch, database):
    code = pairing.start(MAC, "1.1.1.1")
    pairing.approve(code, 7)

    now = time.monotonic()
    monkeypatch.setattr(pairing.time, "monotonic", lambda: now + pairing.GOOD_FOR + 1)
    assert pairing.describe(code) is None
    assert (await pairing.collect(code, "1.1.1.1")).status == pairing.GONE

def test_one_address_cannot_start_pairings_all_day():
    for _ in range(pairing.MOST_STARTS):
        assert pairing.start(MAC, "6.6.6.6") is not None
    assert pairing.start(MAC, "6.6.6.6") is None
    assert pairing.start(MAC, "1.1.1.1") is not None

def test_the_room_for_pairings_is_finite(monkeypatch):
    monkeypatch.setattr(pairing, "MOST_PENDING", 3)
    monkeypatch.setattr(pairing, "MOST_STARTS", 100)
    for _ in range(3):
        assert pairing.start(MAC, "1.1.1.1") is not None
    assert pairing.start(MAC, "1.1.1.1") is None

async def test_guessing_codes_is_refused_and_a_real_poll_is_not(database):
    mine = pairing.start(MAC, "1.1.1.1")
    for _ in range(pairing.MOST_MISSES):
        assert (await pairing.collect("22222222", "6.6.6.6")).status == pairing.GONE
    assert (await pairing.collect("22222222", "6.6.6.6")).status == pairing.THROTTLED
    assert (await pairing.collect(mine, "6.6.6.6")).status == pairing.WAITING
    assert (await pairing.collect("22222222", "1.1.1.1")).status == pairing.GONE

def test_old_addresses_are_forgotten(monkeypatch):
    pairing.start(MAC, "6.6.6.6")
    assert "6.6.6.6" in pairing._starts
    now = time.monotonic()
    monkeypatch.setattr(pairing.time, "monotonic", lambda: now + pairing.STARTS_WINDOW + 1)
    pairing._sweep()
    assert "6.6.6.6" not in pairing._starts

@pytest_asyncio.fixture
async def farm(monkeypatch):
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "the-old-one")
    app = web.Application()
    app.add_routes(farm_http.make_routes())
    served = TestClient(TestServer(app))
    await served.start_server()
    yield served
    await served.close()

async def test_the_application_asks_and_is_told_a_code_and_a_link(farm):
    pairing.set_bot_username("osu1984bot")
    reply = await farm.post("/render/pair", json={
        "name": MAC.name, "os": MAC.os, "cores": MAC.cores, "build": MAC.build,
    })
    assert reply.status == 200
    said = await reply.json()
    assert len(said["code"]) == invites.CODE_LENGTH + 1 and "-" in said["code"]
    assert said["link"] == f"https://t.me/osu1984bot?start=pair-{invites.tidy(said['code'])}"
    assert said["expires_in"] == int(pairing.GOOD_FOR)

    found = pairing.describe(said["code"])
    assert found.machine == MAC

async def test_a_machine_with_no_name_cannot_be_described_so_is_not_started(farm):
    reply = await farm.post("/render/pair", json={"os": "Windows", "cores": 4})
    assert reply.status == 400
    assert pairing.pending() == 0

async def test_odd_fields_do_not_break_it(farm):
    reply = await farm.post("/render/pair", json={"name": "x", "cores": "many"})
    assert reply.status == 200
    assert pairing.describe((await reply.json())["code"]).machine.cores == 0

    reply = await farm.post("/render/pair", data=b"not json")
    assert reply.status == 400
    reply = await farm.post("/render/pair", json=["a", "list"])
    assert reply.status == 400

async def test_the_whole_way_from_code_to_a_working_token(farm, database):
    started = await (await farm.post("/render/pair", json={"name": "drejk"})).json()
    code = started["code"]

    reply = await farm.get(f"/render/pair/{code}")
    assert reply.status == 200 and (await reply.json()) == {"status": "waiting"}

    assert pairing.approve(code, 7, "Naum") is not None

    reply = await farm.get(f"/render/pair/{code}")
    assert reply.status == 200
    said = await reply.json()
    assert said["status"] == "linked"
    token = said["token"]

    hello = await farm.get(
        "/render/hello",
        headers={"Authorization": f"Bearer {token}", "X-Render-Worker": "drejk"},
    )
    assert hello.status == 200

    reply = await farm.get(f"/render/pair/{code}")
    assert reply.status == 404
    assert "token" not in await reply.text()

async def test_too_many_starts_from_one_address_answer_429(farm, monkeypatch):
    monkeypatch.setattr(pairing, "MOST_STARTS", 2)
    for _ in range(2):
        assert (await farm.post("/render/pair", json={"name": "x"})).status == 200
    assert (await farm.post("/render/pair", json={"name": "x"})).status == 429

async def test_too_many_wrong_codes_answer_429(farm, monkeypatch):
    monkeypatch.setattr(pairing, "MOST_MISSES", 2)
    for _ in range(2):
        assert (await farm.get("/render/pair/22222222")).status == 404
    assert (await farm.get("/render/pair/22222222")).status == 429

def test_the_address_behind_a_proxy_is_the_one_the_proxy_added(monkeypatch):
    behind = types.SimpleNamespace(headers={"X-Forwarded-For": "6.6.6.6, 9.9.9.9"},
                                   remote="127.0.0.1")
    assert farm_http._address(behind) == "9.9.9.9"
    monkeypatch.setattr(farm_http, "TRUSTED_PROXY_HOPS", 2)
    two = types.SimpleNamespace(headers={"X-Forwarded-For": "6.6.6.6, 9.9.9.9, 10.0.0.1"},
                                remote="127.0.0.1")
    assert farm_http._address(two) == "9.9.9.9"
    monkeypatch.setattr(farm_http, "TRUSTED_PROXY_HOPS", 5)
    assert farm_http._address(behind) == "6.6.6.6"
    direct = types.SimpleNamespace(headers={}, remote="8.8.8.8")
    assert farm_http._address(direct) == "8.8.8.8"

class _Chat:
    def __init__(self, kind="private"):
        self.type = kind

class _Message:
    def __init__(self, user_id=7, chat="private"):
        self.from_user = types.SimpleNamespace(id=user_id, first_name="Naum", full_name="Naum R")
        self.chat = _Chat(chat)
        self.sent = []

    async def answer(self, text, **kw):
        self.sent.append((text, kw))

    async def edit_text(self, text, **kw):
        self.sent.append((text, kw))

class _Callback:
    def __init__(self, data, user_id=7):
        self.data = data
        self.from_user = types.SimpleNamespace(id=user_id, first_name="Naum", full_name="Naum R")
        self.message = _Message(user_id)
        self.answered = []

    async def answer(self, *args, **kw):
        self.answered.append((args, kw))

@pytest.fixture
def allowed(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "RENDER_OPEN_TO_ALL", False)
    monkeypatch.setattr(settings, "RENDER_TESTER_IDS", [7])

    async def english(_user_id):
        return "en"

    async def strangers(_bot, _telegram_id):
        return False

    from bot.handlers.start import handlers

    monkeypatch.setattr(handlers, "get_language", english)
    monkeypatch.setattr(handlers.members, "shares_a_group", strangers)
    return handlers

def _command(args):
    return types.SimpleNamespace(args=args)

async def test_the_link_shows_a_card_naming_the_machine(allowed):
    code = pairing.start(MAC, "1.1.1.1")
    message = _Message()
    await allowed.start_pairing(message, _command(f"pair-{code}"))

    text, kw = message.sent[-1]
    assert MAC.name in text and "10 cores" in text and "0.11.0" in text
    buttons = [b.callback_data for row in kw["reply_markup"].inline_keyboard for b in row]
    assert buttons == [f"pair:yes:{code}", f"pair:no:{code}"]

async def test_a_dead_code_gets_a_sentence_and_no_buttons(allowed):
    message = _Message()
    await allowed.start_pairing(message, _command("pair-22222222"))
    text, kw = message.sent[-1]
    assert "reply_markup" not in kw
    assert "expired" in text

async def test_somebody_not_allowed_to_render_cannot_pair(allowed):
    code = pairing.start(MAC, "1.1.1.1")
    message = _Message(user_id=8)
    await allowed.start_pairing(message, _command(f"pair-{code}"))
    text, kw = message.sent[-1]
    assert "reply_markup" not in kw
    assert pairing.describe(code).linked_to is None

async def test_a_group_is_not_where_this_happens(allowed):
    code = pairing.start(MAC, "1.1.1.1")
    message = _Message(chat="supergroup")
    await allowed.start_pairing(message, _command(f"pair-{code}"))
    text, kw = message.sent[-1]
    assert "reply_markup" not in kw
    assert pairing.describe(code).linked_to is None

async def test_yes_links_it_to_the_person_who_pressed(allowed):
    code = pairing.start(MAC, "1.1.1.1")
    callback = _Callback(f"pair:yes:{code}")
    await allowed.answer_pairing(callback)

    assert pairing.describe(code).linked_to == 7
    assert pairing.describe(code).linked_name == "Naum R"
    text, _ = callback.message.sent[-1]
    assert MAC.name in text
    assert callback.answered

async def test_no_drops_it(allowed):
    code = pairing.start(MAC, "1.1.1.1")
    callback = _Callback(f"pair:no:{code}")
    await allowed.answer_pairing(callback)
    assert pairing.describe(code) is None
    assert "Not added" in callback.message.sent[-1][0]

async def test_a_press_from_somebody_not_allowed_changes_nothing(allowed):
    code = pairing.start(MAC, "1.1.1.1")
    callback = _Callback(f"pair:yes:{code}", user_id=8)
    await allowed.answer_pairing(callback)
    assert pairing.describe(code).linked_to is None
    assert callback.message.sent == []
    assert callback.answered[0][1].get("show_alert") is True

async def test_a_stale_yes_says_so_instead_of_pretending(allowed):
    callback = _Callback("pair:yes:22222222")
    await allowed.answer_pairing(callback)
    assert "expired" in callback.message.sent[-1][0]

def test_the_pair_link_is_caught_before_the_plain_start():
    from bot.handlers.start import handlers

    names = [h.callback.__name__ for h in handlers.router.message.handlers]
    assert names.index("start_pairing") < names.index("send_welcome_command")


async def test_a_member_of_a_group_may_pair_and_a_stranger_may_not(monkeypatch):
    from bot.handlers.start import handlers

    async def shares(bot, telegram_id):
        return telegram_id == 7

    monkeypatch.setattr(handlers, "can_use_render", lambda telegram_id: telegram_id == 9)
    monkeypatch.setattr(handlers.members, "shares_a_group", shares)
    assert await handlers._may_pair(None, 7)
    assert await handlers._may_pair(None, 9)
    assert not await handlers._may_pair(None, 8)
