import types

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.render_farm import http

TOKEN = "a-shared-secret"
MINE = {"Authorization": f"Bearer {TOKEN}", "X-Render-Worker": "mac"}

@pytest_asyncio.fixture
async def farm(monkeypatch):
    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", TOKEN)
    app = web.Application()
    app.add_routes(http.make_routes())
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client
    await client.close()

async def test_a_stranger_gets_nothing(farm):
    assert (await farm.get("/render/hello")).status == 401
    assert (await farm.get("/render/hello", headers={
        "Authorization": "Bearer wrong", "X-Render-Worker": "mac"})).status == 401

async def test_a_worker_has_to_say_who_it_is(farm):
    reply = await farm.get("/render/hello", headers={"Authorization": f"Bearer {TOKEN}"})
    assert reply.status == 400

async def test_hello_agrees_with_any_application_build(farm):
    got = await (await farm.get("/render/hello?engine=0.12.0", headers=MINE)).json()
    assert {key: got[key] for key in ("build", "agree", "reason")} == {"build": "", "agree": True, "reason": ""}
    assert got["most"] > 0 and "waiting" in got

async def test_the_farm_hands_out_work_again_but_codes_stay_gone(farm):
    assert (await farm.post("/render/join", headers=MINE)).status in (404, 405)
    assert (await farm.post("/render/job/x/result", headers=MINE)).status == 409
    listed = await farm.get("/render/farm", headers=MINE)
    assert listed.status == 200

async def test_without_a_secret_the_endpoints_do_not_exist(monkeypatch):
    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "")
    app = web.Application()
    assert http.install(app) is False
    assert len(app.router.routes()) == 0

async def _member_ok(chat_id, telegram_id):
    return types.SimpleNamespace(status="member")

async def _member_left(chat_id, telegram_id):
    return types.SimpleNamespace(status="left")

class _Sent:
    def __init__(self):
        self.calls = []

    async def send_video(self, chat_id, video, **kwargs):
        with open(video.path, "rb") as handle:
            body = handle.read()
        self.calls.append((chat_id, body, kwargs))
        return types.SimpleNamespace(message_id=42)

    async def get_chat(self, chat_id):
        return types.SimpleNamespace(username="naumredlo", first_name="Naum", last_name="Redlo", photo=None)

@pytest_asyncio.fixture
async def linked(monkeypatch, farm):
    from services.render_farm import invites

    client = farm
    token = "a-personal-token-of-sixty-four-characters-more-or-less-long-x"
    invites.remember(token, invites.Owner(7, "Naum"))
    bot = _Sent()
    http.set_bot(bot)
    yield client, token, bot
    http.set_bot(None)
    invites.forget(invites.digest(token))

async def test_a_machine_can_ask_who_it_belongs_to(linked):
    client, token, _ = linked
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    got = await (await client.get("/render/me", headers=mine)).json()
    assert got["telegram_id"] == 7
    assert got["username"] == "naumredlo"
    assert got["name"] == "Naum Redlo"

async def test_the_shared_secret_belongs_to_no_one(farm):
    client = farm
    assert (await client.get("/render/me", headers=MINE)).status == 404

async def test_a_video_goes_to_the_person_who_linked_the_machine(linked):
    client, token, bot = linked
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac",
            "X-Render-Meta": '{"caption": "NaumRedlo — Daisuke", "name": "d.mp4", "width": 1920, "height": 1080, "duration": 20}'}
    reply = await client.post("/render/send", headers=mine, data=b"mp4-bytes")
    assert reply.status == 200
    assert (await reply.json())["message_id"] == 42
    chat_id, body, kwargs = bot.calls[0]
    assert chat_id == 7 and body == b"mp4-bytes"
    assert kwargs["caption"] == "NaumRedlo — Daisuke" and kwargs["width"] == 1920

async def test_a_video_too_big_for_telegram_is_refused_before_sending(linked, monkeypatch):
    client, token, bot = linked
    monkeypatch.setattr(http, "_max_send_bytes", lambda: 4)
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    reply = await client.post("/render/send", headers=mine, data=b"mp4-bytes")
    assert reply.status == 413
    assert bot.calls == []

async def test_the_chats_list_starts_with_the_private_one(linked, monkeypatch):
    client, token, bot = linked
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    rows = await (await client.get("/render/me/chats", headers=mine)).json()
    assert rows[0] == {"id": 7, "title": "Naum", "private": True, "photo": True}

async def test_a_stranger_has_no_chats(farm):
    client = farm
    assert (await client.get("/render/me/chats", headers=MINE)).status == 404

async def test_a_video_can_go_to_a_chat_the_person_is_in(linked, monkeypatch):
    client, token, bot = linked
    monkeypatch.setattr(bot, "get_chat_member", _member_ok, raising=False)
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac", "X-Render-Meta": '{"chat": -100}'}
    reply = await client.post("/render/send", headers=mine, data=b"mp4")
    assert reply.status == 200
    assert bot.calls[0][0] == -100

async def test_a_chat_the_person_left_is_refused(linked, monkeypatch):
    client, token, bot = linked
    monkeypatch.setattr(bot, "get_chat_member", _member_left, raising=False)
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac", "X-Render-Meta": '{"chat": -200}'}
    assert (await client.post("/render/send", headers=mine, data=b"mp4")).status == 403
    assert bot.calls == []

async def test_a_chat_photo_is_refused_for_a_chat_the_person_left(linked, monkeypatch):
    client, token, _ = linked
    monkeypatch.setattr(_Sent, "get_chat_member", staticmethod(_member_left), raising=False)
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    assert (await client.get("/render/chat/-300/avatar", headers=mine)).status == 403

async def test_odd_bodies_are_read_as_empty_not_as_a_crash(linked):
    client, token, bot = linked
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac", "X-Render-Meta": "[1, 2]"}
    reply = await client.post("/render/send", headers=mine, data=b"mp4")
    assert reply.status == 200
    assert bot.calls[0][0] == 7
    assert (await client.post("/render/pair", json=["not", "a", "dict"])).status == 400
    assert (await client.post("/render/pair", data=b"\xff\xfe")).status == 400

async def test_a_video_s_name_and_numbers_are_cleaned(linked):
    client, token, bot = linked
    mine = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac",
            "X-Render-Meta": '{"name": "../../etc/x.mp4", "width": "wide", "duration": 20.7}'}
    assert (await client.post("/render/send", headers=mine, data=b"mp4")).status == 200
    kwargs = bot.calls[0][2]
    assert kwargs["width"] is None and kwargs["duration"] == 20

def test_a_video_s_numbers_are_checked():
    assert http._dimension(1280) == 1280
    assert http._dimension(12.7) == 12
    assert http._dimension("1280") is None
    assert http._dimension(True) is None
    assert http._dimension(-5) is None
    assert http._dimension(float("nan")) is None
