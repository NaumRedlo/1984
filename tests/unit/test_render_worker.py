"""The worker's side of the bargain: call the engine right, and survive.

Both tests here are about the same evening. A reel went out to the laptop and
the worker died on `exhibit() got an unexpected keyword argument 'threads'` —
one command had grown the resource controls and the other had not, and the
worker calls the two identically. That is the first test.

The second is the more expensive half. The mistake was small and the damage was
not: the exception was not one of the ones `_render` catches, so it escaped to
`main`, killed the process, and left the job leased to a machine that no longer
existed. The bot then waited out the lease before rendering it itself. A worker
has to hand back what it cannot do and keep answering.
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
