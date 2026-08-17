"""The worker's side of the bargain: call the engine right, survive, and let go.

Three groups, each from something that actually happened on the laptop this is
written for.

A reel went out to it and the worker died on `exhibit() got an unexpected
keyword argument 'threads'` — one engine command had grown the resource controls
and the other had not, and the worker calls the two identically.

That mistake was small and the damage was not: the exception was not one of the
ones `_render` catches, so it escaped to `main`, killed the process, and left the
job leased to a machine that no longer existed. A worker has to hand back what it
cannot do and keep answering.

And a replay sent from out of the house was rendered at home into nothing: the
Mac slept partway through, the lease ran out, and the laptop woke to finish a
render nobody would collect. Two answers — hold the machine awake for exactly as
long as the engine runs, and stop rendering the moment the job stops being ours.
"""

import ast
import asyncio
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.dossier import maps, runner  # noqa: E402

WORKER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "render_worker.py",
)


def _engine_call_keywords() -> set[str]:
    """Every keyword the worker hands the engine, read off the call itself.

    Taken from the source rather than written down here, so that a keyword
    added at the call site is checked against both commands without anyone
    remembering to update this file — which is exactly what did not happen.
    """
    tree = ast.parse(open(WORKER).read())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "engine"
        ):
            return {kw.arg for kw in node.keywords if kw.arg}
    raise AssertionError("the worker no longer calls the engine as `engine(...)`")


@pytest.mark.parametrize("command", ["video", "exhibit"])
def test_both_engine_commands_accept_the_call_the_worker_makes(command):
    """The worker picks between `video` and `exhibit` by one word in the job and
    calls whichever it got the same way. So they do not merely resemble each
    other — the call has to fit both, or half the renders crash."""
    signature = inspect.signature(getattr(runner, command))
    missing = _engine_call_keywords() - set(signature.parameters)
    assert not missing, f"runner.{command} does not take {sorted(missing)}"


def test_the_resource_controls_reached_the_reel_too():
    """Named on their own because they are the ones that were missing, and
    because a reel that ignores them is a laptop rendering at full tilt on
    battery — the thing the whole policy exists to prevent."""
    for command in ("video", "exhibit"):
        takes = set(inspect.signature(getattr(runner, command)).parameters)
        assert {"threads", "encoder_threads", "polite"} <= takes, command


# ── surviving a bug ───────────────────────────────────────────────────────

class FakeServer:
    """Just the calls `_render` makes of it."""

    def __init__(self) -> None:
        self.handed_back: list[tuple[str, str]] = []

    async def fetch_replay(self, job_id, into):
        open(into, "wb").write(b"")

    async def heartbeat(self, job_id, progress=None):
        return True

    async def give_back(self, job_id, reason):
        self.handed_back.append((job_id, reason))


class Capacity:
    take, reason, threads, encoder_threads, polite = True, "idle", 4, 2, False


def _run_one_job(monkeypatch, failure: BaseException):
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_worker", WORKER)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    async def inspect_replay(_path):
        return {"beatmap_hash": "abc"}

    async def ensure_map(_api, _hash):
        return None

    async def explode(*_args, **_kw):
        raise failure

    monkeypatch.setattr(worker.runner, "inspect", inspect_replay)
    monkeypatch.setattr(worker.maps, "ensure_map", ensure_map)
    monkeypatch.setattr(worker.maps, "songs_dir", lambda: "/tmp")
    monkeypatch.setattr(worker.runner, "video", explode)
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)

    server = FakeServer()
    job = {"id": "j1", "title": "x", "assets": [], "settings": {"kind": "video"}}
    asyncio.run(worker._render(server, job, Capacity(), None))
    return server


def test_a_bug_in_the_worker_hands_the_job_back_rather_than_killing_it(monkeypatch):
    """A `TypeError` is not a render failing, it is this code being wrong — and
    the worker used to let it out. Held to the same ending as every other
    failure, because the bot's fallback only works on a job it gets back."""
    server = _run_one_job(monkeypatch, TypeError("unexpected keyword argument"))
    assert [job for job, _ in server.handed_back] == ["j1"]
    assert "unexpected keyword" in server.handed_back[0][1]


def test_a_render_that_fails_the_expected_way_still_says_only_what_went_wrong(monkeypatch):
    """The ordinary path is unchanged: the engine's own message goes back as it
    stands, with nothing about the worker wrapped around it."""
    server = _run_one_job(monkeypatch, runner.DossierError("карта не открывается"))
    assert server.handed_back == [("j1", "карта не открывается")]


def test_the_worker_is_still_standing_afterwards(monkeypatch):
    """Two jobs in a row, the first of them a crash. The point is not that the
    second succeeds — it is that there is a second at all."""
    server = _run_one_job(monkeypatch, TypeError("boom"))
    again = _run_one_job(monkeypatch, maps.MapUnavailable("нет карты"))
    assert len(server.handed_back) == 1 and len(again.handed_back) == 1


# ── a laptop that falls asleep ────────────────────────────────────────────
#
# Reported from a real evening: a replay sent from out of the house, rendered at
# home, and the file never arrived. A sleeping process is frozen rather than
# killed, so the heartbeats stop, the bot's lease runs out and it renders the
# job itself — and the laptop wakes minutes later, finishes a render nobody is
# waiting for, and posts it into a job that is no longer its own.

def test_the_machine_is_held_awake_for_exactly_the_render(monkeypatch):
    """`caffeinate` wraps the engine rather than being switched on and off
    around it, so the assertion cannot outlive the render — a worker must not be
    able to leave a machine unable to sleep."""
    from services.dossier import machine

    monkeypatch.setattr(machine.sys, "platform", "darwin")
    monkeypatch.setattr(machine.os, "access", lambda *_: True)
    assert machine.wakeful()[0].endswith("caffeinate")
    assert "-i" in machine.wakeful(), "idle sleep"
    assert "-s" in machine.wakeful(), "and system sleep, which is the reported case"


def test_nothing_is_wrapped_round_a_render_on_a_server(monkeypatch):
    """Linux has no single equivalent, and a server has no business asleep."""
    from services.dossier import machine

    monkeypatch.setattr(machine.sys, "platform", "linux")
    assert machine.wakeful() == ()


def test_a_missing_caffeinate_is_not_an_error(monkeypatch):
    """A macOS without it is not a machine that cannot render."""
    from services.dossier import machine

    monkeypatch.setattr(machine.sys, "platform", "darwin")
    monkeypatch.setattr(machine.os, "access", lambda *_: False)
    assert machine.wakeful() == ()


def test_both_engine_commands_take_the_wrapper():
    """Same lesson as the thread counts: the worker calls one or the other by a
    word in the job, so an argument that reaches only `video` breaks every
    reel."""
    import inspect

    for command in ("video", "exhibit"):
        takes = set(inspect.signature(getattr(runner, command)).parameters)
        assert "prefix" in takes, command


def test_losing_the_job_mid_render_stops_the_render(monkeypatch):
    """It used to change nothing: a flag was set and the engine drew on for
    minutes, on battery, for a file the bot would refuse. The render is
    cancelled now, which the engine already answers by killing the process."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_worker", WORKER)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    cancelled = asyncio.Event()

    async def slow_render(*_args, **_kw):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("the render was allowed to finish")

    async def inspect_replay(_path):
        return {"beatmap_hash": "abc"}

    class Sleeper(FakeServer):
        """A bot that has already given the job to somebody else."""

        async def heartbeat(self, job_id, progress=None):
            return False

    monkeypatch.setattr(worker.runner, "inspect", inspect_replay)
    monkeypatch.setattr(worker.maps, "ensure_map", lambda *_: asyncio.sleep(0))
    monkeypatch.setattr(worker.maps, "songs_dir", lambda: "/tmp")
    monkeypatch.setattr(worker.runner, "video", slow_render)
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.01)

    server = Sleeper()
    job = {"id": "j1", "title": "x", "assets": [], "settings": {"kind": "video"}}
    asyncio.run(worker._render(server, job, Capacity(), None))

    assert cancelled.is_set(), "the engine was left running"
    assert server.handed_back == [], "there is nothing to hand back — it is gone"
