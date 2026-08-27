"""One job, from this bot's queue to a real render client and back.

Every other test of the farm asks one side a question and checks its answer.
This one runs both: the bot's own HTTP endpoints against the client out of the
`dossier` package — the same code a friend downloads — over a real socket.

It matters more than it used to. The bot's own host was the safety net: a job
nobody claimed was rendered here a few seconds later, so a fault in the
handing-over showed up as slowness rather than as silence. Rendering now
happens on the friends' machines and nowhere else, which makes this path the
only path, and a file that quietly fails to arrive is a video that never comes.

What is checked is the delivery and not the drawing: the engine is stubbed, and
what has to be true is that the replay arrives byte for byte, that every asset
the job names arrives, that the settings pointing at those assets are rewritten
to where they actually landed, that the machine says it is alive while it
works, and that the finished file gets back.
"""

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

from dossier import worker as client  # noqa: E402
from services.render_farm import http as farm_http  # noqa: E402
from services.render_farm.queue import RenderQueue  # noqa: E402

TOKEN = "s3cret"

# What a real job carries: the replay, a skin as a zip, and the pictures for
# the scoreboard down the left. The names are the server's own and the job
# refers to each of them as `{{a0}}` and such.
REPLAY = b"osr-bytes-that-must-arrive-unchanged" * 64
def _a_real_skin() -> bytes:
    """A `.osk` with one file in it. Real rather than a handful of bytes
    beginning `PK`, because the worker actually unpacks this — a fake one only
    proves that a warning is logged."""
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
    """The bot's side, on a real socket, with one job waiting."""
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", TOKEN)
    # The claim compares builds. Both sides say the same thing here; the
    # refusal when they do not is `test_engine_build.py`'s subject.
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
            # The map the replay was played on, which is the one thing a
            # worker cannot work out for itself.
            "beatmap": {"id": 129891, "beatmapset_id": 39804},
            # Templated: the worker swaps each for where the file landed.
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
    """The real thing rather than a stand-in.

    A hand-rolled namespace is a copy of a shape, and it drifts: this test
    first failed because the client sends `capacity.code` with a claim and the
    stand-in had never heard of it. `machine.Capacity` cannot fall behind
    itself.
    """
    from dossier.machine import Capacity

    return Capacity(
        take=True, reason="idle", threads=4, encoder_threads=2,
        code="idle", detail="", polite=False,
    )


@pytest_asyncio.fixture
def engine(monkeypatch, tmp_path):
    """The engine, stubbed. What it was handed is what this test is about."""
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
        # While the working directory still exists. Everything the engine was
        # pointed at is readable here and nowhere after this returns.
        board = how.get("leaderboard")
        if board and os.path.isfile(board):
            handed["leaderboard"] = open(board, "rb").read()
        # Something has to arrive at the other end, and the bytes are checked
        # there — an empty file would pass a test that only counted requests.
        with open(out, "wb") as handle:
            handle.write(b"a small mp4, but a real one as far as this goes")
        if how.get("on_progress"):
            await how["on_progress"](types.SimpleNamespace(
                done=30, total=60, fps=42.0, seconds_left=1.0, clip=None,
            ))
        # The real type. The first version of this returned a namespace with
        # a `path` on it, and the client — which reads `report`, `width`,
        # `height` and `duration` off it to tell Telegram the video's shape —
        # handed the job back saying the worker had failed.
        from dossier.runner import RenderResult

        return RenderResult(report=["30 frames"], width=1280, height=720, duration=1)

    monkeypatch.setattr(client.runner, "inspect", inspect)
    monkeypatch.setattr(client.maps, "ensure_known", known)
    monkeypatch.setattr(client.maps, "songs_dir", lambda: str(tmp_path))
    monkeypatch.setattr(client.runner, "video", video)
    monkeypatch.setattr(client, "HEARTBEAT_SECONDS", 0.05)
    return handed


async def _do_the_job(served, engine_stub):
    """Claim what is waiting and do it, exactly as a running worker would."""
    async with client.Server(str(served.make_url("")), TOKEN, "тестовая") as server:
        job = await server.claim("dossier 0.1.0 (abc1234)", _capacity())
        assert job is not None, "the bot offered nothing"
        delivered = await client._render(server, job, _capacity())
    return job, delivered


# ── the delivery ─────────────────────────────────────────────────────────────


async def test_a_waiting_job_is_offered_and_carries_its_map(farm, engine):
    """The map's numbers travel with the job. Turning a replay's hash into a
    beatmap is the only thing that ever needed an osu! account, and a job that
    carries the answer is a worker that needs none — which is the setup step
    most people got wrong, gone."""
    served, _ = farm
    job, _ = await _do_the_job(served, engine)
    assert job["settings"]["beatmap"] == {"id": 129891, "beatmapset_id": 39804}
    assert engine["beatmap"] == {"id": 129891, "beatmapset_id": 39804}


async def test_the_replay_arrives_byte_for_byte(farm, engine):
    """It is the one file the render is *of*. A truncated one renders happily
    and produces the wrong video."""
    served, _ = farm
    await _do_the_job(served, engine)
    assert engine["replay"] == REPLAY
    assert hashlib.sha256(engine["replay"]).digest() == hashlib.sha256(REPLAY).digest()


async def test_every_asset_the_job_names_arrives_where_the_settings_point(
    farm, engine, monkeypatch
):
    """The job says `{{a0}}` and the worker swaps it for the path the file
    landed at. A name left templated is a file the server did not send, and
    the engine would be handed the literal `{{a0}}` as a filename."""
    served, _ = farm
    landed = {}

    def unpacked(settings, here):
        # Read now rather than afterwards: the worker renders into a temporary
        # directory and removes it when the job ends, so a path kept past that
        # point names nothing. Which is correct of the worker and was wrong of
        # the first version of this test.
        landed.update({
            name: open(path, "rb").read() for name, path in here.items()
        })
        return here.get("a0")

    monkeypatch.setattr(client, "_localised_skin", unpacked)
    await _do_the_job(served, engine)

    assert set(landed) == {"a0", "a1"}, landed
    assert landed["a0"] == SKIN, "the skin did not arrive whole"
    assert landed["a1"] == PICTURE, "the scoreboard's picture did not arrive whole"

    # And the setting that pointed at one of them now points at where it
    # actually landed. A name left templated would reach the engine as the
    # literal `{{a1}}` and be opened as a filename.
    board = engine["how"]["leaderboard"]
    assert board and "{{" not in board, f"a name was never swapped: {board}"
    assert engine["leaderboard"] == PICTURE


async def test_the_settings_reach_the_engine_as_the_bot_meant_them(farm, engine):
    """Size and frame rate are chosen by whoever asked for the render. A worker
    that quietly used its own would return a video nobody ordered."""
    served, _ = farm
    await _do_the_job(served, engine)
    assert engine["how"]["size"] == "1280x720"
    assert engine["how"]["fps"] == 60
    # And what the *machine* decided, which is the worker's own business.
    assert engine["how"]["threads"] == 4
    assert engine["how"]["encoder_threads"] == 2


async def test_the_finished_video_gets_back_and_the_job_is_done(farm, engine):
    served, queue = farm
    _job, delivered = await _do_the_job(served, engine)
    assert delivered is True
    assert not queue.waiting(), "the job is still on offer after being delivered"


async def test_the_worker_says_it_is_alive_while_it_works(farm, engine):
    """A render says nothing for long stretches while it encodes, so silence
    has to be reported deliberately rather than inferred. Without this the bot
    takes the job back mid-render and hands it to somebody else."""
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
    assert was is None or True  # the import above is only for the attribute check


# ── what happens when it cannot ──────────────────────────────────────────────


async def test_a_job_naming_no_map_comes_back_before_a_byte_is_fetched(
    farm, engine, monkeypatch
):
    """Seen in a live log: the worker took the job, downloaded the replay,
    unpacked a five-megabyte skin, and only then found the job named no map —
    then did the same again for every retry."""
    served, queue = farm
    queue._jobs.clear() if hasattr(queue, "_jobs") else None
    replay = os.path.join(os.path.dirname(__file__), "..", "..", "README.md")
    queue.offer(replay, "a map", {"kind": "video"})  # no `beatmap`

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
    """A job the worker could not do has to become available again, or one
    machine's bad afternoon is a render nobody ever gets."""
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
    """The token is the whole of the authorisation. Anything that answers
    without it is an open door onto somebody's replays."""
    served, _ = farm
    for method in (served.get, served.post):
        reply = await method(path)
        if reply.status != 405:  # wrong verb for this route; the other one runs
            assert reply.status in (401, 403), f"{path} answered {reply.status}"
