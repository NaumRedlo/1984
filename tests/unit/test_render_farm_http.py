import types

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.render_farm import http
from services.render_farm.queue import RenderQueue

TOKEN = "a-shared-secret"
MINE = {"Authorization": f"Bearer {TOKEN}", "X-Render-Worker": "mac"}
SETTINGS = {"size": "1280x720", "fps": 60, "mute": False, "skin": None}

@pytest_asyncio.fixture
async def farm(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", TOKEN)
    queue = RenderQueue()
    app = web.Application()
    app.add_routes(http.make_routes(queue))
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, queue, tmp_path
    await client.close()

def offer(queue, tmp_path, body=b"osr-bytes"):
    replay = tmp_path / "replay.osr"
    replay.write_bytes(body)
    return queue.offer(str(replay), "a map", SETTINGS)

async def test_a_stranger_gets_nothing(farm):
    client, queue, tmp_path = farm
    offer(queue, tmp_path)
    assert (await client.post("/render/claim")).status == 401
    assert (await client.post("/render/claim", headers={
        "Authorization": "Bearer wrong", "X-Render-Worker": "mac"})).status == 401

async def test_a_worker_has_to_say_who_it_is(farm):
    client, _, _ = farm
    reply = await client.post("/render/claim",
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert reply.status == 400

async def test_without_a_secret_the_endpoints_do_not_exist(monkeypatch):
    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "")
    app = web.Application()
    assert http.install(app) is False
    assert len(app.router.routes()) == 0

async def test_nothing_to_do_answers_no_content(farm):
    client, _, _ = farm
    assert (await client.post("/render/claim", headers=MINE)).status == 204

async def test_a_claim_carries_everything_the_render_needs(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    got = await (await client.post("/render/claim", headers=MINE)).json()
    assert got["id"] == job.id
    assert got["settings"] == SETTINGS

async def test_the_replay_comes_down_to_whoever_holds_the_job(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path, b"the-replay")
    await client.post("/render/claim", headers=MINE)
    reply = await client.get(f"/render/job/{job.id}/replay", headers=MINE)
    assert reply.status == 200 and await reply.read() == b"the-replay"

async def test_somebody_else_s_replay_is_not_downloadable(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    theirs = {"Authorization": f"Bearer {TOKEN}", "X-Render-Worker": "other"}
    assert (await client.get(f"/render/job/{job.id}/replay", headers=theirs)).status == 409

async def test_the_scoreboard_s_pictures_come_down_by_name(farm):
    client, queue, tmp_path = farm
    face = tmp_path / "av.png"
    face.write_bytes(b"a-face")
    replay = tmp_path / "replay.osr"
    replay.write_bytes(b"osr")
    job = queue.offer(str(replay), "a map", SETTINGS, assets={"a0": str(face)})

    claimed = await (await client.post("/render/claim", headers=MINE)).json()
    assert claimed["assets"] == ["a0"]
    reply = await client.get(f"/render/job/{job.id}/file/a0", headers=MINE)
    assert reply.status == 200 and await reply.read() == b"a-face"

async def test_a_name_the_job_never_offered_is_not_a_file(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    for name in ("a0", "..%2F..%2Fetc%2Fpasswd", "etc"):
        reply = await client.get(f"/render/job/{job.id}/file/{name}", headers=MINE)
        assert reply.status == 404, name

async def test_a_replay_that_has_been_cleaned_up_says_so(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    (tmp_path / "replay.osr").unlink()
    assert (await client.get(f"/render/job/{job.id}/replay", headers=MINE)).status == 410

async def test_a_delivered_render_settles_the_job(farm):
    import json
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    reply = await client.post(
        f"/render/job/{job.id}/result", data=b"mp4-bytes",
        headers={**MINE, "X-Render-Meta": json.dumps({"width": 1280, "report": ["ok"]})},
    )
    assert reply.status == 200
    assert job.settled.is_set() and not job.withdrawn
    assert open(job.payload["path"], "rb").read() == b"mp4-bytes"
    assert job.payload["meta"]["width"] == 1280

async def test_a_render_for_a_job_that_is_no_longer_yours_is_refused(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    queue.withdraw(job.id)
    reply = await client.post(f"/render/job/{job.id}/result", data=b"mp4", headers=MINE)
    assert reply.status == 409

async def test_unreadable_meta_does_not_lose_the_video(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    reply = await client.post(f"/render/job/{job.id}/result", data=b"mp4",
                              headers={**MINE, "X-Render-Meta": "{not json"})
    assert reply.status == 200 and job.payload["meta"] == {}

async def test_giving_a_job_back_puts_it_in_the_queue_again(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    reply = await client.post(f"/render/job/{job.id}/give-back", headers=MINE,
                              json={"reason": "battery"})
    assert reply.status == 200
    assert queue.claim("other") is not None

async def test_a_heartbeat_for_a_lost_job_tells_the_worker_to_stop(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    queue.withdraw(job.id)
    reply = await client.post(f"/render/job/{job.id}/heartbeat", headers=MINE, json={})
    assert reply.status == 409 and (await reply.json())["yours"] is False

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

    client, queue, tmp_path = farm
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
    client, _, _ = farm
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
    client, _, _ = farm
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

async def test_odd_bodies_are_read_as_empty_not_as_a_crash(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    assert (await client.post("/render/claim", headers=MINE, json=["not", "a", "dict"])).status == 200
    beat = await client.post(f"/render/job/{job.id}/heartbeat", headers=MINE, json={"progress": [1, 2]})
    assert beat.status == 200
    assert job.progress is None
    assert (await client.post(f"/render/job/{job.id}/heartbeat", headers=MINE, data=b"\xff\xfe")).status == 200
    assert (await client.post("/render/claim", headers=MINE, json={"capacity": 3})).status in (204, 409)
    result = await client.post(f"/render/job/{job.id}/result",
                               headers={**MINE, "X-Render-Meta": "[1, 2]"}, data=b"video")
    assert result.status == 200
    assert job.payload["meta"] == {}

def test_a_video_s_numbers_are_checked():
    assert http._dimension(1280) == 1280
    assert http._dimension(12.7) == 12
    assert http._dimension("1280") is None
    assert http._dimension(True) is None
    assert http._dimension(-5) is None
    assert http._dimension(float("nan")) is None
