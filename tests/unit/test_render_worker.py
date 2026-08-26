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
import types

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
    monkeypatch.setattr(worker.maps, "ensure_known", ensure_map)
    monkeypatch.setattr(worker.maps, "songs_dir", lambda: "/tmp")
    monkeypatch.setattr(worker.runner, "video", explode)
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)

    server = FakeServer()
    # A job carries the map's numbers now, the way the bot sends them.
    job = {"id": "j1", "title": "x", "assets": [],
           "settings": {"kind": "video", "beatmap": {"id": 7, "beatmapset_id": 42}}}
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


def test_a_linux_without_systemd_is_not_a_machine_that_cannot_render(monkeypatch):
    """It is wrapped on Linux too now — `systemd-inhibit`, the same shape and
    the same scoping. A box without it renders anyway rather than refusing."""
    from services.dossier import machine

    monkeypatch.setattr(machine.sys, "platform", "linux")
    monkeypatch.setattr(machine.shutil, "which", lambda _: None)
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
    monkeypatch.setattr(worker.maps, "ensure_known", lambda *_: asyncio.sleep(0))
    monkeypatch.setattr(worker.maps, "songs_dir", lambda: "/tmp")
    monkeypatch.setattr(worker.runner, "video", slow_render)
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.01)

    server = Sleeper()
    # A job carries the map's numbers now, the way the bot sends them.
    job = {"id": "j1", "title": "x", "assets": [],
           "settings": {"kind": "video", "beatmap": {"id": 7, "beatmapset_id": 42}}}
    asyncio.run(worker._render(server, job, Capacity(), None))

    assert cancelled.is_set(), "the engine was left running"
    assert server.handed_back == [], "there is nothing to hand back — it is gone"


# ── a worker on somebody else's machine ───────────────────────────────────
#
# The policy is one decision — `machine.decide` — taking plain numbers. What
# differs per platform is only how those numbers are obtained, so these test the
# reading and not the deciding.

def test_a_linux_battery_is_read_from_its_two_files():
    from services.dossier import machine

    assert machine.parse_linux_battery("42", "Discharging") == (True, 42)
    # `Full`, `Charging` and `Idle` all mean the wall is helping. Only
    # `Discharging` does not.
    assert machine.parse_linux_battery("100", "Full") == (False, 100)
    assert machine.parse_linux_battery("87", "Charging") == (False, 87)


def test_a_machine_that_will_not_say_is_treated_as_plugged_in():
    """Refusing to render on a host that reports nothing is worse than
    rendering on a laptop that happens to be on mains — a desktop and a server
    both have no battery at all."""
    from services.dossier import machine

    assert machine.parse_linux_battery("", "") == (False, 100)
    assert machine.parse_linux_battery("nonsense", "Unknown") == (False, 100)


def test_a_percent_outside_the_range_is_brought_back_into_it():
    from services.dossier import machine

    assert machine.parse_linux_battery("140", "Discharging")[1] == 100
    assert machine.parse_linux_battery("-5", "Discharging")[1] == 0


def test_the_policy_is_the_same_decision_on_every_platform(monkeypatch):
    """Three platforms, one `decide`. A second copy of the thresholds would be
    one copy and a future disagreement about when a laptop is too low to
    render."""
    from services.dossier import machine

    for platform_name in ("darwin", "win32", "linux"):
        monkeypatch.setattr(machine.sys, "platform", platform_name)
        monkeypatch.setattr(machine, "_run", lambda *_: "")
        monkeypatch.setattr(machine, "_windows_battery", lambda: (True, 5))
        monkeypatch.setattr(machine, "_windows_idle_seconds", lambda: 900.0)
        monkeypatch.setattr(machine, "_linux_battery", lambda: (True, 5))
        monkeypatch.setattr(machine, "parse_battery", lambda _: (True, 5))
        # Five per cent on battery is below the floor on any of them.
        assert not machine.capacity(8).take, platform_name


def test_asking_for_less_of_the_machine_does_not_assume_a_path():
    """`nice` is POSIX and is not on Windows, and its path is not the same on
    every Linux. A worker that cannot find it competes on equal terms, which is
    what every worker did before there was a policy."""
    from services.dossier import runner

    prefix = runner._polite_prefix()
    assert prefix == () or (prefix[0].endswith("nice") and prefix[1:] == ("-n", "10"))


def test_the_worker_names_itself_without_os_uname():
    """`os.uname` does not exist on Windows, and the worker is meant to run on
    whatever machine somebody has spare."""
    # Read as code rather than as text: the comment above the line explains why
    # `os.uname` is not used, and a search for the word finds the explanation.
    called = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(ast.parse(open(WORKER).read()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "os.uname" not in called
    assert "platform.node" in called


# ── the setup, which is the part somebody else has to get through ────────────
#
# The farm is about to be several machines that are not mine, and everything
# below is a thing that cost an evening on one of them: a secret retyped into a
# shell, a refusal that named one problem at a time, and a build mismatch that
# killed the worker outright.


def _worker_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_worker", WORKER)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


def test_the_settings_can_live_in_a_file_instead_of_a_shell(tmp_path, monkeypatch):
    worker = _worker_module()
    written = tmp_path / "worker.env"
    written.write_text(
        "# a worker\n"
        "RENDER_SERVER=https://example.org\n"
        "\n"
        'RENDER_WORKER_TOKEN="quoted-because-it-was-pasted"\n'
        "OSU_CLIENT_ID = 12345 \n"
    )
    for key in ("RENDER_SERVER", "RENDER_WORKER_TOKEN", "OSU_CLIENT_ID"):
        monkeypatch.delenv(key, raising=False)

    assert worker.load_config(str(written)) == str(written)
    assert os.environ["RENDER_SERVER"] == "https://example.org"
    assert os.environ["RENDER_WORKER_TOKEN"] == "quoted-because-it-was-pasted"
    assert os.environ["OSU_CLIENT_ID"] == "12345", "spaces round the = are not the value"


def test_a_variable_set_for_one_run_beats_the_file(tmp_path, monkeypatch):
    """Somebody exporting a different server is being deliberate. A config that
    overrode that would be a config with no way round it."""
    worker = _worker_module()
    written = tmp_path / "worker.env"
    written.write_text("RENDER_SERVER=https://the-file.example\n")
    monkeypatch.setenv("RENDER_SERVER", "https://the-shell.example")

    worker.load_config(str(written))
    assert os.environ["RENDER_SERVER"] == "https://the-shell.example"


def test_no_config_file_is_not_a_failure(tmp_path):
    """A worker on a server has its variables from systemd and never wants one."""
    assert _worker_module().load_config(str(tmp_path / "nothing-here")) is None


def test_a_build_mismatch_stands_by_rather_than_killing_the_worker(monkeypatch):
    """It used to be fatal, and being fatal meant every change to the engine
    killed every worker on the farm at once — quietly, on machines nobody was
    watching, while the bot went on rendering everything itself.

    So: stand by, ask the binary again, and come back when it agrees. The
    rebuild is the fix and a worker that resumes by itself afterwards is the
    difference between a farm and a chore."""
    worker = _worker_module()
    asked = []

    class Server:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def claim(self, engine, capacity=None):
            asked.append(engine)
            if engine == "stale":
                raise worker.BuildMismatch("the bot renders with aaa and this worker bbb")
            raise SystemExit("agreed, and that is all this test needed")

    # First answer stale, then — as though somebody rebuilt — the right one.
    answers = iter(["stale", "stale", "fresh"])
    monkeypatch.setattr(worker, "Server", lambda *_a, **_k: Server())
    monkeypatch.setattr(worker.engine_build, "local",
                        lambda **_kw: _resolved(next(answers)))
    monkeypatch.setattr(worker.machine, "capacity", lambda _cores, **_kw: Capacity())
    monkeypatch.setattr(worker, "MISMATCH_SECONDS", 0)
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)

    options = types.SimpleNamespace(server="x", name="w", once=False,
                                    polite=False, threads=0,
                                    config="/nonexistent")
    with pytest.raises(SystemExit):
        asyncio.run(worker._watch(options, "token", None))

    assert asked == ["stale", "stale", "fresh"], (
        "the worker either died on the mismatch or never asked its binary again"
    )


def test_one_shot_still_gives_up_on_a_mismatch(monkeypatch):
    """Nobody is watching a `--once` run to see it recover, and a script that
    hangs for ever instead of failing is worse than one that fails."""
    worker = _worker_module()

    class Server:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def claim(self, _engine, _capacity=None):
            raise worker.BuildMismatch("they differ")

    monkeypatch.setattr(worker, "Server", lambda *_a, **_k: Server())
    monkeypatch.setattr(worker.engine_build, "local", lambda **_kw: _resolved("x"))
    monkeypatch.setattr(worker.machine, "capacity", lambda _cores, **_kw: Capacity())
    monkeypatch.setattr(worker, "MISMATCH_SECONDS", 0)

    options = types.SimpleNamespace(server="x", name="w", once=True,
                                    polite=False, threads=0,
                                    config="/nonexistent")
    with pytest.raises(SystemExit, match="cannot take work"):
        asyncio.run(worker._watch(options, "token", None))


def _resolved(value):
    async def answer():
        return value

    return answer()


def test_a_failure_that_keeps_happening_is_named(monkeypatch, caplog):
    """A machine handing back every job is usually missing a program, not
    failing at rendering — and the exception says so only to somebody who
    already knew."""
    worker = _worker_module()
    server = _run_one_job(monkeypatch, runner.DossierError("ffmpeg: not found"))
    assert server.handed_back, "the job still goes back"

    import logging

    with caplog.at_level(logging.WARNING):
        worker.hint(runner.DossierError("ffmpeg: not found"))
    assert "PATH" in caplog.text


def test_the_check_asks_the_bot_without_taking_anybodys_replay():
    """`--check` reaches a real server. The endpoint it uses has to answer the
    same two questions `claim` does — is this token good, do the builds agree —
    and must not hand back a job while doing it."""
    from services.render_farm import http as farm

    routes = {route.path for route in farm.make_routes()}
    assert "/render/hello" in routes


# ── limits that change while the worker runs ─────────────────────────────────
#
# The point of these is that a machine can be handed back to its owner without
# stopping anything. They are deliberately not read through the environment:
# once a value is in `os.environ` a second read cannot change it, which is the
# exact opposite of what these four are for.


def _options(tmp_path, **over):
    base = dict(polite=False, threads=0, config=str(tmp_path / "worker.env"))
    base.update(over)
    return types.SimpleNamespace(**base)


def test_a_machine_can_be_paused_from_a_text_editor(tmp_path):
    worker = _worker_module()
    written = tmp_path / "worker.env"
    options = _options(tmp_path)

    written.write_text("RENDER_PAUSE=1\n")
    assert worker.asked_for(str(written), options).closed(13) == "paused by its owner"

    # Un-paused, without anything being restarted.
    written.write_text("RENDER_PAUSE=0\n")
    assert worker.asked_for(str(written), options).closed(13) is None


def test_the_file_can_take_back_what_the_command_line_said(tmp_path):
    """`--polite` at launch still means what it says, and can be undone without
    a restart. The command line is the starting position, not the last word."""
    worker = _worker_module()
    written = tmp_path / "worker.env"
    options = _options(tmp_path, polite=True, threads=4)

    written.write_text("")
    assert worker.asked_for(str(written), options).polite is True

    written.write_text("RENDER_POLITE=0\nRENDER_THREADS=16\n")
    later = worker.asked_for(str(written), options)
    assert later.polite is False and later.threads == 16


def test_the_limits_are_not_read_through_the_environment(tmp_path, monkeypatch):
    """The bug this shape exists to avoid: `load_config` refuses to overwrite
    an existing variable, so a second read through it can never change
    anything — and these are the four that have to."""
    worker = _worker_module()
    written = tmp_path / "worker.env"
    monkeypatch.setenv("RENDER_PAUSE", "1")

    written.write_text("RENDER_PAUSE=0\n")
    assert worker.asked_for(str(written), _options(tmp_path)).paused is False, (
        "a stale environment variable outranked the file"
    )


def test_no_file_leaves_the_command_line_standing(tmp_path):
    worker = _worker_module()
    options = _options(tmp_path, polite=True, threads=6)
    got = worker.asked_for(str(tmp_path / "nothing-here"), options)
    assert got.polite is True and got.threads == 6 and got.closed(13) is None


def test_a_paused_worker_still_says_hello(monkeypatch):
    """It has to. Declining by going quiet is what made a laptop on battery
    look exactly like a laptop that was shut — see the farm roster."""
    worker = _worker_module()
    heard = []

    class Server:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def claim(self, _engine, capacity=None):
            heard.append(capacity)
            raise SystemExit("said hello, and that is all this test needed")

    monkeypatch.setattr(worker, "Server", lambda *_a, **_k: Server())
    monkeypatch.setattr(worker.engine_build, "local", lambda **_kw: _resolved("x"))
    monkeypatch.setattr(worker, "asked_for",
                        lambda *_a: worker.machine.Limits(paused=True))
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)

    options = types.SimpleNamespace(server="x", name="w", once=False,
                                    polite=False, threads=0, config="/nonexistent")
    with pytest.raises(SystemExit):
        asyncio.run(worker._watch(options, "token", None))

    assert heard and heard[0].take is False
    assert heard[0].reason == "paused by its owner", (
        "the farm view needs the reason, not just the refusal"
    )


def test_the_loop_re_reads_the_limits_rather_than_remembering_them(tmp_path):
    """The feature itself, and the one thing the tests above cannot see: they
    call `asked_for` directly, so a loop that read it once at startup would
    pass every one of them and still be useless.

    Here the file changes between two polls, the way it does when somebody
    edits it, and the worker has to notice without being restarted.
    """
    import importlib.util
    import pytest as _pytest

    spec = importlib.util.spec_from_file_location("render_worker", WORKER)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    written = tmp_path / "worker.env"
    written.write_text("RENDER_PAUSE=1\n")
    said = []

    class Server:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def claim(self, _engine, capacity=None):
            said.append(capacity.take)
            if len(said) == 1:
                # Somebody un-pauses it between one poll and the next.
                written.write_text("RENDER_PAUSE=0\n")
                return None
            raise SystemExit("two polls is the whole of the test")

    import types as _types

    worker.Server = lambda *_a, **_k: Server()
    worker.engine_build.local = lambda **_kw: _resolved("x")
    worker.machine.capacity = lambda _cores, **_kw: Capacity()
    worker.POLL_SECONDS = 0
    worker.RESTING_SECONDS = 0

    options = _types.SimpleNamespace(server="x", name="w", once=False,
                                     polite=False, threads=0, config=str(written))
    with _pytest.raises(SystemExit):
        asyncio.run(worker._watch(options, "token", None))

    assert said == [False, True], (
        f"the worker read its limits once and kept them: {said}"
    )


# ── a failure somebody else has to read ──────────────────────────────────────
#
# From the first report by somebody running a worker of their own: the job came
# back with a wall of `--kit`, `--pitch`, `--decay`, `--level`, `-h` and nothing
# else. That is the tail of the engine's own help, and the line saying what was
# actually wrong had been cut off the top of it.


def test_an_engine_that_refuses_to_start_reports_why_and_not_its_usage():
    from services.dossier.runner import _why_it_failed

    report = [
        "dossier: `video` has no option `--meter` — see `dossier video --help`",
        "dossier video [OPTIONS] <replay.osr>",
        "Options:",
        "--game-sounds <dir>      osu!'s own sounds, for what a skin leaves out",
        "--kit <name>             click, soft, drum, glass or wood",
        "--pitch <x>              multiply every hit-sound frequency",
        "--decay <x>              multiply every hit-sound decay",
        "--level <x>              multiply hit-sound loudness",
        "-h, --help               this text",
    ]
    said = _why_it_failed(report)
    assert "has no option `--meter`" in said
    # Not "no `--help` anywhere": the complaint itself ends with "see `dossier
    # video --help`", which is the engine pointing at where to look and is the
    # most useful half of the line.
    for listed in ("--kit", "--pitch", "--decay", "--level", "--game-sounds"):
        assert listed not in said, (
            f"the usage is an appendix, never a reason:\n{said}"
        )
    assert said.count("\n") == 0, f"one line was enough:\n{said}"


def test_an_engine_that_dies_mid_render_still_reports_its_last_words():
    """The other end, and the one the old code was written for. Both have to
    keep working — the reason lives at whichever end the engine stopped at."""
    from services.dossier.runner import _why_it_failed

    report = [
        "reading the replay",
        "judging",
        "drawing",
        "the map's audio would not decode: unsupported codec",
    ]
    assert "unsupported codec" in _why_it_failed(report)


def test_an_engine_that_says_nothing_useful_still_says_something():
    from services.dossier.runner import _why_it_failed

    assert _why_it_failed(["-h, --help    this text"]), (
        "dropping every line left nothing at all to report"
    )


def test_a_file_saved_by_notepad_is_read_whole(tmp_path):
    """Notepad writes a byte-order mark and nothing on Windows mentions it.

    With plain utf-8 that mark lands on the front of the first key, so
    `RENDER_SERVER` arrives as `﻿RENDER_SERVER` and is silently not the
    key anybody meant — while the file looks perfect in the editor.
    """
    worker = _worker_module()
    written = tmp_path / "worker.env"
    written.write_bytes(
        "﻿RENDER_SERVER=https://example.org\nRENDER_WORKER_TOKEN=abc\n".encode()
    )
    pairs = worker.read_pairs(str(written))
    assert pairs.get("RENDER_SERVER") == "https://example.org"
    assert pairs.get("RENDER_WORKER_TOKEN") == "abc"


def test_a_line_copied_out_of_a_browser_is_read(tmp_path):
    """A no-break space is what a web page leaves behind, and it is not what
    `strip()` removes."""
    worker = _worker_module()
    written = tmp_path / "worker.env"
    written.write_text(" RENDER_WORKER_TOKEN = abc\n")
    assert worker.read_pairs(str(written)).get("RENDER_WORKER_TOKEN") == "abc"


def test_the_check_says_which_keys_the_file_gave_it():
    """"token: missing" beside a config marked `[+]` reads as the file having
    been ignored, and sends somebody to check the file they just wrote instead
    of the line they left out of it."""
    import inspect

    worker = _worker_module()
    source = inspect.getsource(worker.check)
    assert "in that file" in source
    assert "not there: " in source


async def test_the_check_finds_a_token_that_lives_only_in_the_file(tmp_path, capsys):
    """The whole point of the file, and it was reported missing to everybody
    who used it.

    The token was sampled in `main` and handed to `check` — *before* `check`
    loaded the config — so it could only ever be found in the real
    environment. The file was even listed as containing it two lines above the
    complaint.
    """
    worker = _worker_module()
    written = tmp_path / "worker.env"
    written.write_text(
        "RENDER_WORKER_TOKEN=from-the-file\n"
        "OSU_CLIENT_ID=42\nOSU_CLIENT_SECRET=shh\n"
    )
    for key in ("RENDER_WORKER_TOKEN", "RENDER_SERVER"):
        os.environ.pop(key, None)

    options = types.SimpleNamespace(
        config=str(written), server="", name="w", polite=False, threads=0
    )
    await worker.check(options)
    said = capsys.readouterr().out

    assert "token: missing" not in said, said
    # The line names the token by its fingerprint now rather than saying
    # "set" — what matters here is that it was found at all.
    assert "[+] token: " in said and "chars," in said, said


def test_a_path_is_shown_with_this_systems_own_separators(monkeypatch):
    """`C:\\Users\\name/.dossier/worker.env` works and reads as broken — the
    forward slashes are ours, from the constant, and the backslashes are
    Windows'. Asked about within a minute of the first person seeing it."""
    worker = _worker_module()
    shown = worker.where("~/.dossier/worker.env")
    assert "/" not in shown or "\\" not in shown, f"mixed separators: {shown}"


def test_a_token_can_be_compared_without_being_shown():
    """"The token was rejected" says nothing about *which* of the two sides is
    wrong, and the two cannot compare a secret by pasting it into a chat."""
    worker = _worker_module()

    same = worker.fingerprint("a-shared-secret")
    assert same == worker.fingerprint("a-shared-secret"), "the same string, twice"
    assert same != worker.fingerprint("a-different-secret")
    assert "a-shared-secret" not in same, "the fingerprint is not the secret"


def test_a_stray_newline_or_quote_shows_up_in_the_length():
    """The commonest way two tokens differ is a character that came along for
    the ride, and a length one longer than expected says so at a glance."""
    worker = _worker_module()
    plain = worker.fingerprint("abc")
    assert plain.startswith("3 chars")
    assert worker.fingerprint("abc\n").startswith("4 chars")
    assert worker.fingerprint('"abc"').startswith("5 chars")


def test_nothing_is_fingerprinted_as_nothing():
    assert _worker_module().fingerprint("") == "nothing"


def test_the_refusal_carries_the_fingerprint_to_compare():
    import inspect

    worker = _worker_module()
    source = inspect.getsource(worker._ask_the_bot)
    assert "fingerprint(token)" in source
    assert "logs at startup" in source


def test_the_bot_logs_the_same_fingerprint():
    """Both sides or neither: a fingerprint one side prints is not a
    comparison."""
    import inspect

    from bot import main

    source = inspect.getsource(main)
    assert "Render worker token: %d chars, %s" in source


async def test_the_check_says_when_the_environment_is_beating_the_file(
    tmp_path, capsys
):
    """Found the hard way: two fingerprints, both sixty-four characters, and
    both sides certain they had the same string.

    The environment beats the file on purpose — a variable exported for one run
    is somebody being deliberate — but that is invisible from the outside. The
    file holds the right token, the check reports the wrong one, and everybody
    goes on re-checking the file.
    """
    worker = _worker_module()
    written = tmp_path / "worker.env"
    written.write_text("RENDER_WORKER_TOKEN=the-one-in-the-file\n")
    os.environ["RENDER_WORKER_TOKEN"] = "a-stale-one"
    os.environ.pop("RENDER_SERVER", None)
    try:
        options = types.SimpleNamespace(
            config=str(written), server="", name="w", polite=False, threads=0
        )
        await worker.check(options)
        said = capsys.readouterr().out
    finally:
        os.environ.pop("RENDER_WORKER_TOKEN", None)

    assert "from the environment, not from the file" in said, said
    assert worker.fingerprint("the-one-in-the-file") in said, "and what the file holds"
    assert "the-one-in-the-file" not in said, "but never the token itself"


async def test_no_complaint_when_the_two_agree(tmp_path, capsys):
    """A file and a variable saying the same thing is not a problem, and
    saying so would be noise on every properly set up machine."""
    worker = _worker_module()
    written = tmp_path / "worker.env"
    written.write_text("RENDER_WORKER_TOKEN=agreed\n")
    os.environ["RENDER_WORKER_TOKEN"] = "agreed"
    os.environ.pop("RENDER_SERVER", None)
    try:
        options = types.SimpleNamespace(
            config=str(written), server="", name="w", polite=False, threads=0
        )
        await worker.check(options)
        said = capsys.readouterr().out
    finally:
        os.environ.pop("RENDER_WORKER_TOKEN", None)

    assert "from the environment" not in said


# ── the osu! keys, asked of osu! ─────────────────────────────────────────────
#
# `--check` said "osu! api: set", which means non-empty and is not the question
# anybody has. Two workers passed every check and then died on the first real
# run with `invalid_client` — the keys were there and osu! would not have them,
# and the one place built to find that out beforehand had not looked.


async def test_a_client_id_that_is_not_a_number_is_named_rather_than_sent(
    monkeypatch
):
    """The commonest way to get this wrong is to swap the two, and it is worth
    saying so rather than letting osu! answer `invalid_client` about it."""
    worker = _worker_module()
    monkeypatch.setenv("OSU_CLIENT_ID", "aBcDeFgHiJkLmNoPqRsT")
    monkeypatch.setenv("OSU_CLIENT_SECRET", "52339")

    asked = []
    monkeypatch.setattr(worker.aiohttp, "ClientSession",
                        lambda *a, **k: asked.append(1))

    got = await worker._osu_keys()
    assert got.ok is False
    assert "not a number" in got.said
    assert "swapped" in got.fix
    assert not asked, "nothing should have been sent to osu! at all"


async def test_no_keys_at_all_is_not_a_problem_any_more(monkeypatch):
    """The bot sends the map's numbers with the job, so a worker needs no osu!
    account of its own. These are the fallback for a bot too old to send them,
    and a worker without them is not a worker with something to fix — which is
    the whole point of removing the step two people out of three got wrong."""
    worker = _worker_module()
    monkeypatch.delenv("OSU_CLIENT_ID", raising=False)
    monkeypatch.delenv("OSU_CLIENT_SECRET", raising=False)
    got = await worker._osu_keys()
    assert got.ok is None, "optional, not missing"
    assert "not needed" in got.said


async def test_not_reaching_osu_is_not_the_keys_being_wrong(monkeypatch):
    """Nothing was learned about them, and saying they are bad would send
    somebody to make new ones over a dropped connection."""
    worker = _worker_module()
    monkeypatch.setenv("OSU_CLIENT_ID", "52339")
    monkeypatch.setenv("OSU_CLIENT_SECRET", "a-secret")

    class Refuses:
        async def __aenter__(self):
            raise worker.aiohttp.ClientError("no route to host")

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(worker.aiohttp, "ClientSession", lambda *a, **k: Refuses())
    got = await worker._osu_keys()
    assert got.ok is None, "unknown, not wrong"


def test_refused_credentials_end_the_run_with_a_sentence():
    """It arrived as a traceback through four frames of asyncio followed by
    aiohttp complaining about the session nobody got to close, which reads as
    the worker being broken rather than as two lines in a file being wrong."""
    import inspect

    worker = _worker_module()
    source = inspect.getsource(worker.main)
    assert "osu! would not take these credentials" in source
    assert "await api.close()" in source


async def test_a_job_that_names_its_map_needs_no_osu_account(monkeypatch, tmp_path):
    """The change that removes the step. The bot looked the map up to draw the
    card; the job carries what it found, and the worker fetches by number."""
    worker = _worker_module()
    asked = {"known": None, "looked_up": False}

    async def known(beatmap, checksum):
        asked["known"] = beatmap
        return beatmap

    async def looked_up(_api, _checksum):
        asked["looked_up"] = True
        return {}

    async def inspect_replay(_path):
        return {"beatmap_hash": "abc"}

    async def explode(*_a, **_kw):
        raise runner.DossierError("enough — the map was already fetched")

    monkeypatch.setattr(worker.maps, "ensure_known", known)
    monkeypatch.setattr(worker.maps, "ensure_map", looked_up)
    monkeypatch.setattr(worker.runner, "inspect", inspect_replay)
    monkeypatch.setattr(worker.maps, "songs_dir", lambda: str(tmp_path))
    monkeypatch.setattr(worker.runner, "video", explode)
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)

    job = {
        "id": "j1", "title": "x", "assets": [],
        "settings": {"kind": "video", "beatmap": {"id": 7, "beatmapset_id": 42}},
    }
    # `api=None` is the point: a worker with no credentials at all.
    await worker._render(FakeServer(), job, Capacity(), None)

    assert asked["known"] == {"id": 7, "beatmapset_id": 42}
    assert not asked["looked_up"], "it asked osu! anyway"


async def test_a_job_it_cannot_do_is_handed_back_before_anything_is_fetched(
    monkeypatch, tmp_path
):
    """Seen in a live log: the worker took the job, downloaded the replay,
    unpacked a five-megabyte skin, and only then found the job named no map —
    then did the same again for every retry.

    The one thing it needs to know is in the job description, so it can be
    known before a byte moves.
    """
    worker = _worker_module()
    fetched = []

    class Watching(FakeServer):
        async def fetch_replay(self, job_id, into):
            fetched.append("replay")

        async def fetch_asset(self, job_id, name, into):
            fetched.append(name)

    monkeypatch.setattr(worker, "POLL_SECONDS", 0)
    server = Watching()
    job = {
        "id": "j1", "title": "x", "assets": ["a0"],
        # No `beatmap`: what a bot older than this worker sends.
        "settings": {"kind": "video", "skin": "{{a0}}"},
    }
    await worker._render(server, job, Capacity(), None)

    assert not fetched, f"it fetched {fetched} before finding out it could not"
    assert [job for job, _ in server.handed_back] == ["j1"]
    assert "older than this worker" in server.handed_back[0][1], (
        "and it says what to do about it — the message named osu! credentials, "
        "which is not the thing anybody needs to fix"
    )
