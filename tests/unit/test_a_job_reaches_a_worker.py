import asyncio
import hashlib
import os
import sys
import types

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dossier import worker as client
from services.render_farm import http as farm_http
from services.render_farm.queue import RenderQueue

TOKEN = "s3cret"

REPLAY = b"osr-bytes-that-must-arrive-unchanged" * 64
def _a_real_skin() -> bytes:
    import io as _io
    import zipfile as _zipfile

    made = _io.BytesIO()
    with _zipfile.ZipFile(made, "w") as archive:
        archive.writestr("hitcircle.png", b"\x89PNG\r\n\x1a\n a circle")
        archive.writestr("skin.ini", "[General]\nName: тестовый\n")
    return made.getvalue()

SKIN = _a_real_skin()
PICTURE = b"\x89PNG\r\n\x1a\n and some pixels"

@pytest_asyncio.fixture
async def farm(monkeypatch, tmp_path):
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", TOKEN)

    async def local(*_args, **_kwargs):
        return "dossier 0.1.0 (abc1234)"

    monkeypatch.setattr(farm_http.engine_build, "local", local)

    replay = tmp_path / "replay.osr"
    replay.write_bytes(REPLAY)
    skin = tmp_path / "skin.osk"
    skin.write_bytes(SKIN)
    cover = tmp_path / "cover.png"
    cover.write_bytes(PICTURE)

    queue = RenderQueue()
    queue.offer(
        str(replay), "xi - FREEDOM DiVE [Extra]",
        {
            "kind": "video",
            "size": "1280x720",
            "fps": 60,

            "beatmap": {"id": 129891, "beatmapset_id": 39804},

            "skin": "{{a0}}",
            "leaderboard": "{{a1}}",
        },
        assets={"a0": str(skin), "a1": str(cover)},
    )

    app = web.Application()
    app.add_routes(farm_http.make_routes(queue))
    served = TestClient(TestServer(app))
    await served.start_server()
    yield served, queue
    await served.close()

def _capacity():
    from dossier.machine import Capacity

    return Capacity(
        take=True, reason="idle", threads=4, encoder_threads=2,
        code="idle", detail="", polite=False,
    )

@pytest_asyncio.fixture
def engine(monkeypatch, tmp_path):
    handed = {}

    async def inspect(path):
        handed["replay"] = open(path, "rb").read()
        return {"beatmap_hash": "6e7f6f08671ad9a9d2fa079665d8d443"}

    async def known(beatmap, checksum):
        handed["beatmap"] = beatmap
        handed["checksum"] = checksum
        return beatmap

    async def video(replay, songs, out, **how):
        handed["how"] = how

        board = how.get("leaderboard")
        if board and os.path.isfile(board):
            handed["leaderboard"] = open(board, "rb").read()

        with open(out, "wb") as handle:
            handle.write(b"a small mp4, but a real one as far as this goes")
        if how.get("on_progress"):
            await how["on_progress"](types.SimpleNamespace(
                done=30, total=60, fps=42.0, seconds_left=1.0, clip=None,
            ))

        from dossier.runner import RenderResult

        return RenderResult(report=["30 frames"], width=1280, height=720, duration=1)

    monkeypatch.setattr(client.runner, "inspect", inspect)
    monkeypatch.setattr(client.maps, "ensure_known", known)
    monkeypatch.setattr(client.maps, "songs_dir", lambda: str(tmp_path))
    monkeypatch.setattr(client.runner, "video", video)
    monkeypatch.setattr(client, "HEARTBEAT_SECONDS", 0.05)
    return handed

async def _do_the_job(served, engine_stub):
    async with client.Server(str(served.make_url("")), TOKEN, "тестовая") as server:
        job = await server.claim("dossier 0.1.0 (abc1234)", _capacity())
        assert job is not None, "the bot offered nothing"
        delivered = await client._render(server, job, _capacity())
    return job, delivered

async def test_a_waiting_job_is_offered_and_carries_its_map(farm, engine):
    served, _ = farm
    job, _ = await _do_the_job(served, engine)
    assert job["settings"]["beatmap"] == {"id": 129891, "beatmapset_id": 39804}
    assert engine["beatmap"] == {"id": 129891, "beatmapset_id": 39804}

async def test_the_replay_arrives_byte_for_byte(farm, engine):
    served, _ = farm
    await _do_the_job(served, engine)
    assert engine["replay"] == REPLAY
    assert hashlib.sha256(engine["replay"]).digest() == hashlib.sha256(REPLAY).digest()

async def test_every_asset_the_job_names_arrives_where_the_settings_point(
    farm, engine, monkeypatch
):
    served, _ = farm
    landed = {}

    def unpacked(settings, here):

        landed.update({
            name: open(path, "rb").read() for name, path in here.items()
        })
        return here.get("a0")

    monkeypatch.setattr(client, "_localised_skin", unpacked)
    await _do_the_job(served, engine)

    assert set(landed) == {"a0", "a1"}, landed
    assert landed["a0"] == SKIN, "the skin did not arrive whole"
    assert landed["a1"] == PICTURE, "the scoreboard's picture did not arrive whole"

    board = engine["how"]["leaderboard"]
    assert board and "{{" not in board, f"a name was never swapped: {board}"
    assert engine["leaderboard"] == PICTURE

async def test_the_settings_reach_the_engine_as_the_bot_meant_them(farm, engine):
    served, _ = farm
    await _do_the_job(served, engine)
    assert engine["how"]["size"] == "1280x720"
    assert engine["how"]["fps"] == 60

    assert engine["how"]["threads"] == 4
    assert engine["how"]["encoder_threads"] == 2

async def test_the_finished_video_gets_back_and_the_job_is_done(farm, engine):
    served, queue = farm
    _job, delivered = await _do_the_job(served, engine)
    assert delivered is True
    assert not queue.waiting(), "the job is still on offer after being delivered"

async def test_the_worker_says_it_is_alive_while_it_works(farm, engine):
    served, queue = farm
    beats = []
    was = farm_http.RenderQueue.heartbeat if hasattr(farm_http, "RenderQueue") else None
    original = queue.heartbeat

    def counted(*args, **kwargs):
        beats.append(args)
        return original(*args, **kwargs)

    queue.heartbeat = counted
    await _do_the_job(served, engine)
    assert beats, "the bot heard nothing from the worker for the whole render"
    assert was is None or True

async def test_a_job_naming_no_map_comes_back_before_a_byte_is_fetched(
    farm, engine, monkeypatch
):
    served, queue = farm
    queue._jobs.clear() if hasattr(queue, "_jobs") else None
    replay = os.path.join(os.path.dirname(__file__), "..", "..", "README.md")
    queue.offer(replay, "a map", {"kind": "video"})

    fetched = []
    original = client.Server.fetch_replay

    async def watched(self, job_id, into):
        fetched.append(job_id)
        return await original(self, job_id, into)

    monkeypatch.setattr(client.Server, "fetch_replay", watched)

    async with client.Server(str(served.make_url("")), TOKEN, "тестовая") as server:
        job = await server.claim("dossier 0.1.0 (abc1234)", _capacity())
        assert await client._render(server, job, _capacity()) is False

    assert not fetched, "it fetched the replay before finding out it could not"

async def test_the_bot_takes_a_handed_back_job_back(farm, engine, monkeypatch):
    served, queue = farm

    async def explodes(*_args, **_how):
        raise client.runner.DossierError("the engine fell over")

    monkeypatch.setattr(client.runner, "video", explodes)
    async with client.Server(str(served.make_url("")), TOKEN, "тестовая") as server:
        job = await server.claim("dossier 0.1.0 (abc1234)", _capacity())
        assert await client._render(server, job, _capacity()) is False

    await asyncio.sleep(0)
    assert queue.waiting(), "the job was lost rather than handed back"

@pytest.mark.parametrize("path", [
    "/render/hello", "/render/claim", "/render/farm",
])
async def test_the_endpoints_refuse_a_worker_without_the_token(farm, path):
    served, _ = farm
    for method in (served.get, served.post):
        reply = await method(path)
        if reply.status != 405:
            assert reply.status in (401, 403), f"{path} answered {reply.status}"
