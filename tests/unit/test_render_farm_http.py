"""The endpoints a render worker talks to.

These sit on the same public listener as the OAuth callback, so the tests that
matter most are the ones about who is allowed to speak to them at all: an
unguarded render endpoint hands a stranger somebody's replay file and accepts
their idea of what a job is.
"""

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


# ── who may speak ─────────────────────────────────────────────────────────

async def test_a_stranger_gets_nothing(farm):
    client, queue, tmp_path = farm
    offer(queue, tmp_path)
    assert (await client.post("/render/claim")).status == 401
    assert (await client.post("/render/claim", headers={
        "Authorization": "Bearer wrong", "X-Render-Worker": "mac"})).status == 401


async def test_a_worker_has_to_say_who_it_is(farm):
    """Claims are leased to a name; an anonymous one could never be told from
    a second worker reaching for the same job."""
    client, _, _ = farm
    reply = await client.post("/render/claim",
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert reply.status == 400


async def test_without_a_secret_the_endpoints_do_not_exist(monkeypatch):
    """An unset token is not an empty password. Installing these routes with
    nothing guarding them would publish a replay-download endpoint."""
    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "")
    app = web.Application()
    assert http.install(app) is False
    assert len(app.router.routes()) == 0


# ── taking work ───────────────────────────────────────────────────────────

async def test_nothing_to_do_answers_no_content(farm):
    """A worker asks constantly. An empty queue is the normal case, not a 404."""
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
    """The one endpoint that hands out a file somebody uploaded privately."""
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    theirs = {"Authorization": f"Bearer {TOKEN}", "X-Render-Worker": "other"}
    assert (await client.get(f"/render/job/{job.id}/replay", headers=theirs)).status == 409


async def test_a_replay_that_has_been_cleaned_up_says_so(farm):
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    (tmp_path / "replay.osr").unlink()
    assert (await client.get(f"/render/job/{job.id}/replay", headers=MINE)).status == 410


# ── delivering ────────────────────────────────────────────────────────────

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
    """The bot gave up and rendered it. Accepting this would settle a job whose
    video has already been sent, and leave the file behind on disk."""
    client, queue, tmp_path = farm
    job = offer(queue, tmp_path)
    await client.post("/render/claim", headers=MINE)
    queue.withdraw(job.id)
    reply = await client.post(f"/render/job/{job.id}/result", data=b"mp4", headers=MINE)
    assert reply.status == 409


async def test_unreadable_meta_does_not_lose_the_video(farm):
    """The numbers are hints for Telegram's placeholder. Dropping a finished
    render because a header would not parse trades minutes of work for none."""
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
