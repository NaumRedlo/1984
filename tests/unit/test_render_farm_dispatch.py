import asyncio

import pytest

from dossier import runner
from dossier.runner import RenderResult
from services.render_farm import dispatch
from services.render_farm.dispatch import bundle
from services.render_farm.queue import LEASE_SECONDS, RenderQueue

def test_every_path_in_a_scoreboard_becomes_a_name(tmp_path):
    avatar, cover = tmp_path / "av.png", tmp_path / "cv.png"
    avatar.write_bytes(b"a")
    cover.write_bytes(b"c")
    board = f"Naum\t900000\t99.1\tHD\t{avatar}\t{cover}"

    text, mine, assets, _ = bundle(board, (None, None))
    assert text.split("\t")[4:6] == ["{{a0}}", "{{a1}}"]
    assert assets == {"a0": str(avatar), "a1": str(cover)}
    assert mine == ("", "")

def test_a_picture_that_is_not_there_becomes_an_empty_column(tmp_path):
    board = "Naum\t900000\t99.1\t\t/gone/av.png\t"
    text, _, assets, _ = bundle(board, (None, None))
    assert text.split("\t")[4:6] == ["", ""]
    assert assets == {}

def test_the_player_s_own_pictures_travel_the_same_way(tmp_path):
    face = tmp_path / "me.png"
    face.write_bytes(b"m")
    _, mine, assets, _ = bundle(None, (str(face), None))
    assert mine == ("{{a0}}", "") and assets == {"a0": str(face)}

def test_one_picture_mentioned_twice_travels_once(tmp_path):
    face = tmp_path / "me.png"
    face.write_bytes(b"m")
    board = f"Naum\t900000\t99.1\tHD\t{face}\t"
    text, mine, assets, _ = bundle(board, (str(face), None))
    assert assets == {"a0": str(face)}
    assert text.split("\t")[4] == "{{a0}}" and mine[0] == "{{a0}}"

def test_a_render_with_no_scoreboard_sends_no_board(tmp_path):
    text, _, _, _ = bundle(None, (None, None))
    assert text is None

@pytest.fixture
def farm(monkeypatch, tmp_path):
    queue = RenderQueue()
    monkeypatch.setattr(dispatch, "queue", queue)

    from services.render_farm.roster import Roster

    monkeypatch.setattr(dispatch, "roster", Roster())
    monkeypatch.setattr(dispatch, "RENDER_WORKER_TOKEN", "secret")
    monkeypatch.setattr(dispatch, "RENDER_WORKER_WAIT", 0.05)
    monkeypatch.setattr(dispatch, "_TICK", 0.005)

    monkeypatch.setattr(dispatch, "RENDER_GIVE_UP", 30.0)
    monkeypatch.setattr(dispatch, "TELL_EVERY_SECONDS", 0.01)

    done = []

    async def never(*args, **kwargs):
        done.append(args[2])
        raise AssertionError("the bot rendered a job itself")

    monkeypatch.setattr(dispatch.runner, "video", never)
    monkeypatch.setattr(dispatch.runner, "exhibit", never)
    return queue, done, tmp_path

async def render(tmp_path, **over):
    args = dict(replay_path=str(tmp_path / "r.osr"), songs_dir=str(tmp_path),
                out_path=str(tmp_path / "out.mp4"), title="a map")
    args.update(over)
    return await dispatch.video(args.pop("replay_path"), args.pop("songs_dir"),
                                args.pop("out_path"), **args)

async def test_with_no_farm_configured_it_says_so_rather_than_waiting(farm, monkeypatch):
    queue, done, tmp_path = farm
    monkeypatch.setattr(dispatch, "RENDER_WORKER_TOKEN", "")
    with pytest.raises(dispatch.NobodyCame) as refused:
        await render(tmp_path)
    assert "RENDER_WORKER_TOKEN" in str(refused.value)
    assert not queue.waiting(), "nothing should have been offered"
    assert not done

async def test_nobody_claiming_ends_in_being_told_so(farm, monkeypatch):
    queue, done, tmp_path = farm
    monkeypatch.setattr(dispatch, "RENDER_GIVE_UP", 0.1)
    with pytest.raises(dispatch.NobodyCame) as refused:
        await render(tmp_path)
    assert "не на связи" in str(refused.value)
    assert queue.waiting() == [], "the offer must not outlive the wait"
    assert not done

async def test_machines_that_are_here_but_busy_get_a_different_sentence(
    farm, monkeypatch
):
    queue, done, tmp_path = farm
    monkeypatch.setattr(dispatch, "RENDER_GIVE_UP", 0.1)
    dispatch.roster.hello("mac", build="dossier 0.1.0 (abc1234)")
    with pytest.raises(dispatch.NobodyCame) as refused:
        await render(tmp_path)
    assert "не взял" in str(refused.value), str(refused.value)

async def test_a_worker_that_claims_and_dies_puts_the_job_back_on_offer(farm):
    queue, done, tmp_path = farm
    produced = tmp_path / "second-try.mp4"
    produced.write_bytes(b"the machine that stayed up")

    async def one_dies_then_another_works():
        while not queue.waiting():
            await asyncio.sleep(0.005)

        first = queue.claim("mac")
        first.lease_until = 0.0
        while not queue.waiting():
            await asyncio.sleep(0.005)
        second = queue.claim("desktop")
        queue.finish(second.id, "desktop", {
            "path": str(produced), "meta": {"report": ["remote"]},
        })

    task = asyncio.create_task(one_dies_then_another_works())
    result = await render(tmp_path)
    await task
    assert result.report == ["remote"] and not done

async def test_a_scoreboard_render_goes_out_like_any_other(farm):
    queue, done, tmp_path = farm
    avatar = tmp_path / "av.png"
    avatar.write_bytes(b"png")
    board = f"Naum\t900000\t99.1\tHD\t{avatar}\t"
    produced = tmp_path / "made.mp4"
    produced.write_bytes(b"a video")

    offered = []

    async def watch():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")
        offered.append(job)
        queue.finish(job.id, "mac", {"path": str(produced), "meta": {}})

    task = asyncio.create_task(watch())
    await render(tmp_path, leaderboard=board)
    await task

    job = offered[0]
    assert job.settings["leaderboard"], "the board has to travel with the job"
    assert str(avatar) not in job.settings["leaderboard"], (
        "a path from this host means nothing on the worker's"
    )
    assert list(job.assets.values()) == [str(avatar)]
    assert not done

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
    queue, done, tmp_path = farm

    async def work():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")
        queue.finish(job.id, "mac", {"path": str(tmp_path / "never-written.mp4")})

    task = asyncio.create_task(work())
    with pytest.raises(dispatch.NobodyCame) as refused:
        await render(tmp_path)
    await task
    assert "не прислала" in str(refused.value)
    assert not done

async def test_a_worker_keeping_its_lease_is_waited_for(farm):
    queue, done, tmp_path = farm
    produced = tmp_path / "slow.mp4"
    produced.write_bytes(b"eventually")

    async def work():
        while not queue.waiting():
            await asyncio.sleep(0.005)
        job = queue.claim("mac")

        for _ in range(20):
            await asyncio.sleep(0.01)
            assert queue.heartbeat(job.id, "mac")
        queue.finish(job.id, "mac", {"path": str(produced), "meta": {"report": ["slow"]}})

    task = asyncio.create_task(work())
    result = await render(tmp_path)
    await task
    assert result.report == ["slow"] and not done

async def test_progress_from_the_worker_reaches_the_caller(farm):
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
    assert LEASE_SECONDS > 60

async def test_a_reel_goes_out_to_a_worker_like_any_other_render(farm, monkeypatch):
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
    queue, done, tmp_path = farm
    empty = runner.Selection(clips=[], rate=1.0)
    with pytest.raises(runner.DossierError):
        await dispatch.exhibit(
            str(tmp_path / "r.osr"), str(tmp_path), str(tmp_path / "out.mp4"), chosen=empty
        )
    assert not queue.waiting() and not done

async def test_a_reel_nobody_takes_is_refused_like_any_other_render(farm, monkeypatch):
    queue, done, tmp_path = farm
    monkeypatch.setattr(dispatch, "RENDER_GIVE_UP", 0.1)
    chosen = runner.Selection(clips=[
        runner.Moment(from_ms=0.0, to_ms=1000.0, scorer="miss", reason="a miss", detail={}),
    ], rate=1.0)

    with pytest.raises(dispatch.NobodyCame):
        await dispatch.exhibit(
            str(tmp_path / "r.osr"), str(tmp_path), str(tmp_path / "out.mp4"), chosen=chosen
        )
    assert not done

def test_a_skin_travels_as_one_archive_with_its_hash(tmp_path, monkeypatch):
    from dossier import skins as store

    monkeypatch.setattr(store, "SKIN_STORE_DIR", str(tmp_path / "skins"))
    folder = tmp_path / "skins" / "doki"
    folder.mkdir(parents=True)
    (folder / "hitcircle.png").write_bytes(b"a picture")
    (folder / "cursor.png").write_bytes(b"another")

    _, _, assets, (name, digest) = bundle(None, (None, None), str(folder))
    assert name and name.startswith("{{"), name
    assert digest and len(digest) == 16
    assert len(assets) == 1, "one file, not one per picture"
    assert next(iter(assets.values())).endswith(".zip")

def test_the_same_skin_hashes_the_same_way_twice(tmp_path, monkeypatch):
    from dossier import skins as store

    monkeypatch.setattr(store, "SKIN_STORE_DIR", str(tmp_path / "skins"))
    folder = tmp_path / "skins" / "doki"
    folder.mkdir(parents=True)
    (folder / "hitcircle.png").write_bytes(b"a picture")

    first = bundle(None, (None, None), str(folder))[3][1]
    second = bundle(None, (None, None), str(folder))[3][1]
    assert first == second

def test_a_render_without_a_skin_carries_none(tmp_path):
    assert bundle(None, (None, None), None)[3] == (None, None)
    assert bundle(None, (None, None), "classic")[3] == (None, None)

@pytest.fixture
def told(monkeypatch):
    said = []

    async def note(waiting):
        said.append(waiting)

    monkeypatch.setattr(dispatch, "TELL_EVERY_SECONDS", 0.005)
    return said, note

async def test_a_wait_with_no_machines_says_there_are_none(farm, monkeypatch, told):
    queue, _done, tmp_path = farm
    said, note = told
    monkeypatch.setattr(dispatch, "RENDER_GIVE_UP", 0.15)

    with pytest.raises(dispatch.NobodyCame):
        await render(tmp_path, on_queue=note)

    assert said, "nobody was told anything at all"
    assert all(one.workers == 0 for one in said), said

async def test_a_wait_behind_other_jobs_carries_the_place_in_the_line(
    farm, monkeypatch, told
):
    queue, _done, tmp_path = farm
    said, note = told
    monkeypatch.setattr(dispatch, "RENDER_GIVE_UP", 0.15)

    for at in range(2):
        queue.offer(str(tmp_path / f"{at}.osr"), "another", {"kind": "video"})

    with pytest.raises(dispatch.NobodyCame):
        await render(tmp_path, on_queue=note)

    assert said and said[0].ahead == 2, said

async def test_a_job_claimed_straight_away_hears_nothing_about_a_queue(farm, told):
    queue, done, tmp_path = farm
    said, note = told
    produced = tmp_path / "quick.mp4"
    produced.write_bytes(b"fast")

    async def work():
        while not queue.waiting():
            await asyncio.sleep(0.001)
        job = queue.claim("mac")
        queue.finish(job.id, "mac", {"path": str(produced), "meta": {}})

    task = asyncio.create_task(work())
    await render(tmp_path, on_queue=note)
    await task
    assert not said, said
    assert not done

async def test_the_place_counts_only_what_is_actually_in_front(farm, monkeypatch):
    queue, _done, tmp_path = farm
    for at in range(3):
        queue.offer(str(tmp_path / f"{at}.osr"), "another", {"kind": "video"})
    queue.claim("mac")

    mine = queue.offer(str(tmp_path / "mine.osr"), "mine", {"kind": "video"})
    standing = dispatch._where_it_stands(mine)
    assert standing.ahead == 2, standing

async def test_a_job_claimed_between_the_sweep_and_the_look_reads_as_next(farm):
    queue, _done, tmp_path = farm
    mine = queue.offer(str(tmp_path / "mine.osr"), "mine", {"kind": "video"})
    queue.claim("mac")
    assert dispatch._where_it_stands(mine).ahead == 0
