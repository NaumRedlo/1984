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
