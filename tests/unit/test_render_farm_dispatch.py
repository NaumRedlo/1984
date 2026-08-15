"""Choosing which machine renders, and always rendering.

The promise this module has to keep is not "renders happen on the laptop" — it
is "renders happen". The laptop is somebody's laptop and is allowed to be shut,
flat, busy or absent, and every one of those still has to end with a video.
"""

import asyncio

import pytest

from services.dossier.runner import RenderResult
from services.render_farm import dispatch
from services.render_farm.queue import LEASE_SECONDS, RenderQueue


@pytest.fixture
def farm(monkeypatch, tmp_path):
    """A queue of this test's own, a worker token, and a short patience."""
    queue = RenderQueue()
    monkeypatch.setattr(dispatch, "queue", queue)
    monkeypatch.setattr(dispatch, "RENDER_WORKER_TOKEN", "secret")
    monkeypatch.setattr(dispatch, "RENDER_WORKER_WAIT", 0.05)
    monkeypatch.setattr(dispatch, "_TICK", 0.005)

    done = []

    async def locally(*args, **kwargs):
        done.append(args[2])
        with open(args[2], "wb") as handle:
            handle.write(b"rendered here")
        return RenderResult(report=["local"], width=1280, height=720, duration=30)

    monkeypatch.setattr(dispatch.runner, "video", locally)
    return queue, done, tmp_path


async def render(tmp_path, **over):
    args = dict(replay_path=str(tmp_path / "r.osr"), songs_dir=str(tmp_path),
                out_path=str(tmp_path / "out.mp4"), title="a map")
    args.update(over)
    return await dispatch.video(args.pop("replay_path"), args.pop("songs_dir"),
                                args.pop("out_path"), **args)


# ── falling back ──────────────────────────────────────────────────────────

async def test_with_no_worker_configured_it_renders_here_at_once(farm, monkeypatch):
    """The ordinary deployment. Nothing should wait on a feature nobody set up."""
    queue, done, tmp_path = farm
    monkeypatch.setattr(dispatch, "RENDER_WORKER_TOKEN", "")
    result = await render(tmp_path)
    assert result.report == ["local"] and done


async def test_nobody_claiming_ends_in_a_local_render(farm):
    """The laptop is shut. A few seconds later the video is made here anyway."""
    queue, done, tmp_path = farm
    result = await render(tmp_path)
    assert result.report == ["local"] and done
    assert queue.waiting() == [], "the offer must not outlive the render"


async def test_a_worker_that_claims_and_dies_ends_in_a_local_render(farm):
    """The lid closed mid-render. This is the case a lease exists for: without
    it the job sits claimed for ever and somebody watches a still progress bar."""
    queue, done, tmp_path = farm

    async def claim_then_vanish():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        # Claim it, then never speak again. Time is not mocked here, so the
        # lease is stepped past explicitly.
        job = queue.claim("mac")
        job.lease_until = 0.0

    task = asyncio.create_task(claim_then_vanish())
    result = await render(tmp_path)
    await task
    assert result.report == ["local"] and done


async def test_a_scoreboard_render_never_leaves_this_host(farm):
    """The rivals file and the player's own pictures are files here. A remote
    render would silently drop them, which is a worse video, not a faster one."""
    queue, done, tmp_path = farm
    result = await render(tmp_path, leaderboard="someone\t123")
    assert result.report == ["local"] and done
    assert not queue.waiting(), "it was never offered out in the first place"


# ── succeeding elsewhere ──────────────────────────────────────────────────

async def test_a_delivered_render_is_the_one_that_is_used(farm):
    queue, done, tmp_path = farm
    produced = tmp_path / "from-the-worker.mp4"
    produced.write_bytes(b"made on the laptop")

    async def work():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")
        queue.finish(job.id, "mac", {
            "path": str(produced),
            "meta": {"report": ["remote"], "width": 1920, "height": 1080, "duration": 42},
        })

    task = asyncio.create_task(work())
    result = await render(tmp_path)
    await task

    assert not done, "the bot must not render what a worker already rendered"
    assert result.report == ["remote"] and result.width == 1920 and result.duration == 42
    with open(tmp_path / "out.mp4", "rb") as handle:
        assert handle.read() == b"made on the laptop"
    assert not produced.exists(), "moved rather than copied — it was written once already"


async def test_a_worker_that_delivers_nothing_is_not_taken_at_its_word(farm):
    """Settled, but with no file where it said. Believing it would send an
    empty video; the honest answer is to render it here."""
    queue, done, tmp_path = farm

    async def work():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")
        queue.finish(job.id, "mac", {"path": str(tmp_path / "never-written.mp4")})

    task = asyncio.create_task(work())
    result = await render(tmp_path)
    await task
    assert result.report == ["local"] and done


async def test_a_worker_keeping_its_lease_is_waited_for(farm):
    """Longer than the patience for a *claim*: taking the job is a matter of
    seconds, doing it is minutes, and the two deadlines are nothing alike."""
    queue, done, tmp_path = farm
    produced = tmp_path / "slow.mp4"
    produced.write_bytes(b"eventually")

    async def work():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")
        # Well past the claim patience, with the lease kept alive throughout.
        for _ in range(20):
            await asyncio.sleep(0.01)
            assert queue.heartbeat(job.id, "mac")
        queue.finish(job.id, "mac", {"path": str(produced), "meta": {"report": ["slow"]}})

    task = asyncio.create_task(work())
    result = await render(tmp_path)
    await task
    assert result.report == ["slow"] and not done


async def test_progress_from_the_worker_reaches_the_caller(farm):
    """Somebody is watching a message that says how far along it is. That it is
    another machine doing the work is not their business.

    The progress and the finished file are posted back to back, with no pause
    between them — because that is what a real worker does, and a waiter that
    looked for the result before the progress dropped every update that shared
    a tick with it. An end-to-end run found exactly that; the version of this
    test with a sleep in the middle passed straight through it.
    """
    queue, done, tmp_path = farm
    produced = tmp_path / "p.mp4"
    produced.write_bytes(b"x")
    seen = []

    async def watch(told):
        seen.append(told)

    async def work():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")
        queue.heartbeat(job.id, "mac", {"done": 30, "total": 60, "fps": 90.0,
                                        "seconds_left": 4.0, "clip": None})
        queue.finish(job.id, "mac", {"path": str(produced), "meta": {}})

    task = asyncio.create_task(work())
    await render(tmp_path, on_progress=watch)
    await task
    assert seen and seen[0].done == 30 and seen[0].total == 60


async def test_a_lease_kept_alive_survives_the_claim_patience(farm):
    """Guards the clock the last test relies on: LEASE_SECONDS has to be well
    over the interval a worker heartbeats at, or a busy encoder loses its job."""
    assert LEASE_SECONDS > 60
