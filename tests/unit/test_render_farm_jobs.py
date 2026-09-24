import struct

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.render_farm import http as farm_http
from services.render_farm import orders, skins
from services.render_farm.queue import MAX_ATTEMPTS, RenderQueue, State
from services.render_farm.roster import Roster
from utils.osr import header

TOKEN = "s3cret"
HASH = "0123456789abcdef0123456789abcdef"

def _string(words: str) -> bytes:
    raw = words.encode()
    return b"\x0b" + bytes([len(raw)]) + raw

def an_osr(mode: int = 0, player: str = "NaumRedlo", beatmap: str = HASH) -> bytes:
    return (
        struct.pack("<B", mode) + struct.pack("<i", 20260924)
        + _string(beatmap) + _string(player) + _string("f" * 32)
        + struct.pack("<6H", 1695, 9, 0, 120, 4, 0)
        + struct.pack("<i", 12345678) + struct.pack("<H", 1705) + b"\x01" + struct.pack("<i", 72)
        + b"the rest of the replay"
    )

def test_a_replay_header_names_its_map_player_and_mode():
    head = header(an_osr())
    assert head.mode == 0
    assert head.beatmap_md5 == HASH
    assert head.player == "NaumRedlo"
    assert (head.count300, head.count100, head.misses) == (1695, 9, 0)
    assert head.combo == 1705 and head.perfect and head.mods == 72

def test_what_is_not_a_replay_is_refused():
    assert header(b"not a replay at all") is None
    assert header(an_osr(beatmap="nope")) is None
    assert header(b"") is None

def test_the_oldest_waiting_job_is_claimed_first_and_held_by_its_worker():
    line = RenderQueue()
    first = line.offer("a.osr", "first", beatmap_md5=HASH, requester=1, now=0.0)
    second = line.offer("b.osr", "second", beatmap_md5=HASH, requester=2, now=1.0)
    assert line.ahead_of(second, now=1.5) == 1
    taken = line.claim("w1", "MacBook", now=2.0)
    assert taken is first and taken.state is State.CLAIMED and taken.worker_name == "MacBook"
    assert line.ahead_of(second, now=2.5) == 0
    assert not line.heartbeat(first.id, "w2", now=3.0)
    assert line.heartbeat(first.id, "w1", {"done": 10, "total": 100}, now=3.0)
    assert first.progress == {"done": 10, "total": 100}

def test_a_quiet_worker_loses_its_job_and_it_goes_back_in_line():
    line = RenderQueue()
    job = line.offer("a.osr", "t", beatmap_md5=HASH, now=0.0)
    line.claim("w1", now=0.0)
    line.sweep(now=200.0)
    assert job.state is State.WAITING and job.attempts == 1 and job.worker is None

def test_a_job_handed_back_too_often_is_withdrawn():
    line = RenderQueue()
    job = line.offer("a.osr", "t", beatmap_md5=HASH, now=0.0)
    for _ in range(MAX_ATTEMPTS):
        line.claim("w1", now=1.0)
        line.give_back(job.id, "w1", "the map is nowhere")
    assert job.withdrawn and job.settled.is_set() and job.reason == "the map is nowhere"
    assert line.get(job.id) is None

def test_a_finished_job_carries_its_video_and_leaves_the_queue():
    line = RenderQueue()
    job = line.offer("a.osr", "t", beatmap_md5=HASH, requester=7, now=0.0)
    assert line.open_for(7) == 1
    line.claim("w1", now=1.0)
    assert line.finish(job.id, "w1", {"path": "/tmp/v.mp4"})
    assert job.settled.is_set() and job.payload == {"path": "/tmp/v.mp4"}
    assert line.open_for(7) == 0

def test_the_title_names_the_player_and_the_map():
    head = header(an_osr())
    beatmap = {"version": "Grace", "beatmapset_id": 39804, "beatmapset": {"artist": "yaseta", "title": "Bluenation"}}
    assert orders.title_of(head, beatmap) == ("NaumRedlo — yaseta - Bluenation [Grace]", 39804)
    assert orders.title_of(head, None) == ("NaumRedlo", None)

def test_a_skin_on_the_server_is_described_by_its_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(skins.settings, "RENDER_SKINS_DIR", str(tmp_path))
    (tmp_path / "Rafis.osk").write_bytes(b"a skin")
    (tmp_path / "notes.txt").write_text("ignored")
    assert skins.available() == ["Rafis"]
    found = skins.described("Rafis")
    assert found["name"] == "Rafis" and found["size"] == 6 and len(found["hash"]) == 64
    assert skins.described("Missing") is None
    assert skins.described(None) is None

REPLAY = an_osr() * 4
VIDEO = b"\x00\x00\x00\x18ftypmp42 a small video"

@pytest_asyncio.fixture
async def farm(monkeypatch, tmp_path):
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", TOKEN)
    replay = tmp_path / "replay.osr"
    replay.write_bytes(REPLAY)
    skin = tmp_path / "Rafis.osk"
    skin.write_bytes(b"skin bytes")
    line = RenderQueue()
    who = Roster()
    job = line.offer(str(replay), "NaumRedlo — yaseta - Bluenation [Grace]", beatmap_md5=HASH, beatmapset_id=39804,
                     skin={"name": "Rafis", "hash": "ab" * 32, "size": 10, "path": str(skin)}, requester=1, chat_id=-5)
    app = web.Application()
    app.add_routes(farm_http.make_routes(line, who))
    served = TestClient(TestServer(app))
    await served.start_server()
    yield served, line, who, job
    await served.close()

def _as(name: str) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Render-Worker": name}

@pytest.mark.asyncio
async def test_a_job_goes_to_a_worker_and_its_video_comes_back(farm):
    client, line, who, job = farm
    taken = await client.post("/render/claim", json={"build": "0.89.4", "take": True}, headers=_as("MacBook"))
    assert taken.status == 200
    handed = await taken.json()
    assert handed["id"] == job.id and handed["beatmap_md5"] == HASH and handed["beatmapset_id"] == 39804
    assert handed["settings"]["width"] == 1920 and handed["settings"]["height"] == 1080 and handed["settings"]["fps"] == 60
    assert handed["skin"]["name"] == "Rafis" and "path" not in handed["skin"]
    got = await client.get(f"/render/job/{job.id}/replay", headers=_as("MacBook"))
    assert await got.read() == REPLAY
    skin = await client.get(f"/render/job/{job.id}/skin", headers=_as("MacBook"))
    assert await skin.read() == b"skin bytes"
    beat = await client.post(f"/render/job/{job.id}/heartbeat", json={"progress": {"stage": "drawing", "done": 50, "total": 100}}, headers=_as("MacBook"))
    assert (await beat.json())["yours"]
    listed = await (await client.get("/render/farm", headers=_as("MacBook"))).json()
    assert listed["workers"][0]["state"] == "rendering" and listed["workers"][0]["mine"]
    sent = await client.post(f"/render/job/{job.id}/result", data=VIDEO, headers={**_as("MacBook"), "X-Render-Meta": '{"duration": 320}'})
    assert sent.status == 200
    assert job.settled.is_set() and job.payload["meta"]["duration"] == 320
    with open(job.payload["path"], "rb") as video:
        assert video.read() == VIDEO
    assert who.here()[0].delivered == 1

@pytest.mark.asyncio
async def test_another_worker_cannot_touch_a_job_it_did_not_take(farm):
    client, line, who, job = farm
    await client.post("/render/claim", json={}, headers=_as("MacBook"))
    other = await client.get(f"/render/job/{job.id}/replay", headers=_as("PC"))
    assert other.status == 409
    stolen = await client.post(f"/render/job/{job.id}/result", data=VIDEO, headers=_as("PC"))
    assert stolen.status == 409

@pytest.mark.asyncio
async def test_a_resting_worker_is_given_nothing(farm):
    client, line, who, job = farm
    rest = await client.post("/render/claim", json={"take": False}, headers=_as("MacBook"))
    assert rest.status == 204
    assert job.state is State.WAITING
    listed = await (await client.get("/render/farm", headers=_as("MacBook"))).json()
    assert listed["waiting"] == 1 and listed["workers"][0]["state"] == "resting"

@pytest.mark.asyncio
async def test_a_job_handed_back_waits_for_the_next_worker(farm):
    client, line, who, job = farm
    await client.post("/render/claim", json={}, headers=_as("MacBook"))
    back = await client.post(f"/render/job/{job.id}/give-back", json={"reason": "no ffmpeg"}, headers=_as("MacBook"))
    assert (await back.json())["ok"]
    assert job.state is State.WAITING and job.attempts == 1
    again = await client.post("/render/claim", json={}, headers=_as("PC"))
    assert (await again.json())["id"] == job.id

@pytest.mark.asyncio
async def test_a_stranger_gets_nothing(farm):
    client, line, who, job = farm
    refused = await client.post("/render/claim", json={}, headers={"Authorization": "Bearer nope", "X-Render-Worker": "x"})
    assert refused.status == 401

def test_the_status_says_where_the_job_stands(monkeypatch):
    line = RenderQueue()
    who = Roster()
    monkeypatch.setattr(orders, "queue", line)
    monkeypatch.setattr(orders, "roster", who)
    job = line.offer("a.osr", "NaumRedlo — Bluenation", beatmap_md5=HASH)
    assert "подождёт" in orders.status_of(job, "ru")
    who.hello("k", "MacBook")
    assert "впереди: 0" in orders.status_of(job, "ru")
    line.claim("k", "MacBook")
    assert "готовит карту" in orders.status_of(job, "ru")
    line.heartbeat(job.id, "k", {"stage": "drawing", "done": 25, "total": 100, "seconds_left": 75})
    assert "25%" in orders.status_of(job, "ru") and "1:15" in orders.status_of(job, "ru")
