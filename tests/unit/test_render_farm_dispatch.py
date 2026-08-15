"""Choosing which machine renders, and always rendering.

The promise this module has to keep is not "renders happen on the laptop" — it
is "renders happen". The laptop is somebody's laptop and is allowed to be shut,
flat, busy or absent, and every one of those still has to end with a video.
"""

import asyncio

import pytest

from services.dossier import runner
from services.dossier.runner import RenderResult
from services.render_farm import dispatch
from services.render_farm.dispatch import bundle
from services.render_farm.queue import LEASE_SECONDS, RenderQueue


# ── naming the files that have to travel ──────────────────────────────────

def test_every_path_in_a_scoreboard_becomes_a_name(tmp_path):
    """Rows are `name, total, accuracy, mods, avatar, cover`, and the last two
    are paths on the bot's disk."""
    avatar, cover = tmp_path / "av.png", tmp_path / "cv.png"
    avatar.write_bytes(b"a")
    cover.write_bytes(b"c")
    board = f"Naum\t900000\t99.1\tHD\t{avatar}\t{cover}"

    text, mine, assets = bundle(board, (None, None))
    assert text.split("\t")[4:6] == ["{{a0}}", "{{a1}}"]
    assert assets == {"a0": str(avatar), "a1": str(cover)}
    assert mine == ("", "")


def test_a_picture_that_is_not_there_becomes_an_empty_column(tmp_path):
    """The bot draws an empty frame for a player it has no face for, and rows
    arrive with the column already blank. Neither may become the literal path
    of a file that does not exist."""
    board = "Naum\t900000\t99.1\t\t/gone/av.png\t"
    text, _, assets = bundle(board, (None, None))
    assert text.split("\t")[4:6] == ["", ""]
    assert assets == {}


def test_the_player_s_own_pictures_travel_the_same_way(tmp_path):
    face = tmp_path / "me.png"
    face.write_bytes(b"m")
    _, mine, assets = bundle(None, (str(face), None))
    assert mine == ("{{a0}}", "") and assets == {"a0": str(face)}


def test_one_picture_mentioned_twice_travels_once(tmp_path):
    """Whoever played the replay is usually also a row on the board, so their
    avatar is named in both places. Fetching it twice is a round trip spent on
    a file the worker already has."""
    face = tmp_path / "me.png"
    face.write_bytes(b"m")
    board = f"Naum\t900000\t99.1\tHD\t{face}\t"
    text, mine, assets = bundle(board, (str(face), None))
    assert assets == {"a0": str(face)}
    assert text.split("\t")[4] == "{{a0}}" and mine[0] == "{{a0}}"


def test_a_render_with_no_scoreboard_sends_no_board(tmp_path):
    """`None` and an empty string mean different things to the engine: one
    draws no scoreboard, the other writes an empty rivals file."""
    text, _, _ = bundle(None, (None, None))
    assert text is None


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


async def test_a_scoreboard_render_goes_out_like_any_other(farm):
    """It used to stay here, on the grounds that the scoreboard's pictures are
    files on this host. But the bot builds a scoreboard for *every* render, so
    that exception was the rule, and the worker sat idle through all of it.
    The pictures are sent instead — eight rows of thumbnails against a video."""
    queue, done, tmp_path = farm
    avatar = tmp_path / "av.png"
    avatar.write_bytes(b"png")
    board = f"Naum\t900000\t99.1\tHD\t{avatar}\t"

    offered = []

    async def watch():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")
        offered.append(job)
        # Handed straight back: this test is about what the job carries, and
        # a worker that claimed and then said nothing would make it sit here
        # for the whole lease before the bot gave up on it.
        queue.give_back(job.id, "mac", "seen enough")

    task = asyncio.create_task(watch())
    await render(tmp_path, leaderboard=board)
    await task

    job = offered[0]
    assert job.settings["leaderboard"], "the board has to travel with the job"
    assert str(avatar) not in job.settings["leaderboard"], (
        "a path from this host means nothing on the worker's"
    )
    assert list(job.assets.values()) == [str(avatar)]


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


# ── reels ─────────────────────────────────────────────────────────────────

async def test_a_reel_goes_out_to_a_worker_like_any_other_render(farm, monkeypatch):
    """It used to stay here: the protocol carried one render and a reel is
    several cut together. The engine does the cutting, so the only thing that
    had to travel was which command to run."""
    queue, done, tmp_path = farm
    produced = tmp_path / "reel.mp4"
    produced.write_bytes(b"a reel")
    chosen = runner.Selection(clips=[
        runner.Moment(from_ms=0.0, to_ms=1000.0, scorer="miss", reason="a miss", detail={}),
    ], rate=1.0)
    seen = []

    async def work():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")
        seen.append(job.settings["kind"])
        queue.finish(job.id, "mac", {"path": str(produced), "meta": {"report": ["remote"]}})

    task = asyncio.create_task(work())
    result = await dispatch.exhibit(
        str(tmp_path / "r.osr"), str(tmp_path), str(tmp_path / "out.mp4"), chosen=chosen
    )
    await task

    assert seen == ["exhibit"], "the worker is told which command to run"
    assert result.render.report == ["remote"] and not done
    assert result.selection is chosen, (
        "the caller gets back the selection it already showed somebody, "
        "not a second one that happens to agree"
    )


async def test_a_reel_with_nowhere_to_cut_is_refused_before_anyone_is_asked(farm):
    """A replay shorter than one clip has no reel in it. Offering that job out
    would spend a worker's minutes to reach the same answer."""
    queue, done, tmp_path = farm
    empty = runner.Selection(clips=[], rate=1.0)
    with pytest.raises(runner.DossierError):
        await dispatch.exhibit(
            str(tmp_path / "r.osr"), str(tmp_path), str(tmp_path / "out.mp4"), chosen=empty
        )
    assert not queue.waiting() and not done


async def test_a_reel_falls_back_here_with_its_selection_intact(farm, monkeypatch):
    """The fallback runs `exhibit` locally, which answers with both halves —
    and the reel that comes back must still be the one that was chosen."""
    queue, done, tmp_path = farm
    chosen = runner.Selection(clips=[
        runner.Moment(from_ms=0.0, to_ms=1000.0, scorer="miss", reason="a miss", detail={}),
    ], rate=1.0)

    async def locally(*args, **kwargs):
        done.append(args[2])
        return runner.ReelResult(
            RenderResult(report=["local reel"], width=1280, height=720, duration=12),
            kwargs["chosen"],
        )

    monkeypatch.setattr(dispatch.runner, "exhibit", locally)
    result = await dispatch.exhibit(
        str(tmp_path / "r.osr"), str(tmp_path), str(tmp_path / "out.mp4"), chosen=chosen
    )
    assert done and result.render.report == ["local reel"]
    assert result.selection is chosen
