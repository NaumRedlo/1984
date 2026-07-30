"""Talking to the `dossier` binary.

Dossier is Rust; the bot is Python. Rather than bind the two together, the bot
runs the binary and reads a line of JSON back. That keeps the engine testable on
its own (`cargo test`, `dossier judge` on a folder of replays) and means a crash
in the simulator is a non-zero exit code, not a dead bot process.
"""

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import NamedTuple, Optional

from config.settings import (
    DOSSIER_BIN,
    DOSSIER_CRF,
    DOSSIER_ENCODER_THREADS,
    DOSSIER_PRESET,
    DOSSIER_SKIN,
)
from utils.logger import get_logger

logger = get_logger("services.dossier")

# A pathological map or a very long replay shouldn't be able to wedge a handler.
_TIMEOUT_SECONDS = 120

# Rendering is minutes of honest work, not a hung process. Long enough for a
# marathon map, short enough that a wedged encoder still gets cleaned up.
_VIDEO_TIMEOUT_SECONDS = 1800


class DossierError(RuntimeError):
    """The engine couldn't answer. The message is meant to be shown as-is to a
    render tester — they're the only ones who see it."""


def binary_path() -> str:
    return os.path.expanduser(DOSSIER_BIN)


def is_available() -> bool:
    path = binary_path()
    return os.path.isfile(path) and os.access(path, os.X_OK)


async def _launch(args: tuple[str, ...], timeout: int) -> tuple[int, str, str]:
    """Run the binary and hand back everything it said.

    Both streams are returned rather than judged here, because what counts as
    interesting depends on the command: `judge` speaks JSON on stdout, `video`
    writes a file and reports on stderr, and throwing either away on the
    success path is how diagnostics go missing.
    """
    path = binary_path()
    if not is_available():
        raise DossierError(
            f"движок не собран: {path} нет или он не исполняемый.\n"
            "Собрать: cd dossier && cargo build --release"
        )

    try:
        process = await asyncio.create_subprocess_exec(
            path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise DossierError(f"не удалось запустить движок: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise DossierError(f"движок не ответил за {timeout} с")

    return (
        process.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


async def _run(*args: str, timeout: int = _TIMEOUT_SECONDS) -> list[dict]:
    """Run a command that answers in JSON, one object per line."""
    _, stdout, stderr = await _launch(args, timeout)

    # A non-zero exit still carries usable JSON — `judge` fails the run when any
    # replay was skipped, but reports every replay it did manage. So parse
    # first and only complain if there's nothing to show.
    results = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("dossier emitted a non-JSON line: %r", line[:200])

    if not results:
        raise DossierError((stderr.strip() or "движок ничего не вернул")[:500])
    return results


class Progress(NamedTuple):
    """Where a render has got to, as the engine last said."""

    done: int
    total: int
    fps: float
    seconds_left: float

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0


_PROGRESS = re.compile(r"(\d+)/(\d+) frames, ([\d.]+)/s, ([\d.]+)s left")


def _progress_of(chunk: str) -> Progress | None:
    found = _PROGRESS.search(chunk)
    if not found:
        return None
    return Progress(int(found[1]), int(found[2]), float(found[3]), float(found[4]))


async def _launch_watched(
    args: tuple[str, ...],
    timeout: int,
    on_progress: Callable[[Progress], Awaitable[None]] | None,
) -> tuple[int, str]:
    """Run the engine and watch it work.

    `communicate()` hands everything over at the end, which is fine for a
    command that answers in a second and useless for one that runs for minutes:
    the progress it prints is only worth anything while it is still printing.
    So stderr is read as it arrives, progress lines are handed to the caller,
    and the whole of it is kept for the report.

    The ticker redraws one line with carriage returns, so a "line" here is
    whatever arrived between one of those and the next.
    """
    path = binary_path()
    if not is_available():
        raise DossierError(
            f"движок не собран: {path} нет или он не исполняемый.\n"
            "Собрать: cd dossier && cargo build --release"
        )
    try:
        process = await asyncio.create_subprocess_exec(
            path,
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise DossierError(f"не удалось запустить движок: {exc}") from exc

    collected: list[str] = []

    async def pump() -> None:
        buffer = ""
        while True:
            block = await process.stderr.read(4096)
            if not block:
                break
            text = block.decode("utf-8", "replace")
            collected.append(text)
            buffer += text
            # Split on both, because the ticker uses one and everything else
            # uses the other.
            *complete, buffer = re.split(r"[\r\n]", buffer)
            for chunk in complete:
                progress = _progress_of(chunk)
                if progress and on_progress:
                    await on_progress(progress)

    try:
        await asyncio.wait_for(asyncio.gather(pump(), process.wait()), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise DossierError(f"движок не ответил за {timeout} с")
    except asyncio.CancelledError:
        # Somebody pressed cancel. Killing the engine is the whole point — an
        # abandoned render would otherwise keep a core busy for minutes while
        # the bot pretends it stopped, and on a one-core host that is the same
        # as the bot being down.
        process.kill()
        await process.wait()
        raise

    return process.returncode or 0, "".join(collected)


def _report_lines(stderr: str) -> list[str]:
    """The engine's own account of a render, minus the progress ticker.

    Progress redraws one line with carriage returns, so splitting on those as
    well leaves the finished statements and drops the thousand partial ones.
    """
    lines = []
    for chunk in stderr.replace("\r", "\n").splitlines():
        chunk = chunk.strip()
        if chunk and "frames," not in chunk:
            lines.append(chunk)
    return lines


async def inspect(replay_path: str) -> dict:
    """Read the replay header. Needs no beatmap — this is how the caller learns
    which map to fetch."""
    return (await _run("inspect", "--json", replay_path))[0]


async def judge(replay_path: str, songs_dir: str) -> dict:
    """Judge the replay against whatever map in `songs_dir` matches its hash."""
    return (await _run("judge", "--json", "--songs", os.path.expanduser(songs_dir), replay_path))[0]


class RenderResult(NamedTuple):
    """What a finished render was, and what the engine said about making it.

    The dimensions and duration matter beyond the log: Telegram draws a video's
    placeholder from the numbers it is given, not from the stream, so a video
    sent without them arrives square on a phone and only corrects itself once
    playback starts.
    """

    report: list[str]
    width: int | None
    height: int | None
    duration: int | None


_VIDEO_META = re.compile(r"^dossier: video (\d+)x(\d+) ([0-9.]+)s$")


def _video_meta(report: list[str]) -> tuple[int | None, int | None, int | None]:
    """Pull the finished file's shape out of the engine's report.

    Reported by the process that wrote the file rather than measured afterwards
    — it is the one that knows. Absent or malformed is not an error: the video
    still sends, it just goes without the hints.
    """
    for line in report:
        found = _VIDEO_META.match(line.strip())
        if found:
            return int(found[1]), int(found[2]), round(float(found[3]))
    return None, None, None


async def video(
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    size: str = "1280x720",
    fps: int = 60,
    mute: bool = False,
    skin: str | None = None,
    leaderboard: str | None = None,
    my_pictures: tuple[str | None, str | None] = (None, None),
    on_progress: Callable[[Progress], Awaitable[None]] | None = None,
) -> RenderResult:
    """Render the replay to `out_path`.

    Nothing is returned: the engine writes a file and reports progress on
    stderr. Minutes, not seconds — a two-minute map at 720p is around two and a
    half — so this gets its own timeout rather than the one sized for judging.

    The skin comes from settings rather than being fixed here: which look the
    bot renders in is a deployment's decision, not this function's.

    Returns what the engine said about the render — thread count, the timing
    breakdown — which is the only way to tell a render that is slow because the
    encoder is saturated from one that is slow because it is drawing on one
    core. It was being captured and discarded, so nobody could see either.
    """
    args = [
        "video",
        "--skin",
        skin or DOSSIER_SKIN,
        "--preset",
        DOSSIER_PRESET,
        "--crf",
        DOSSIER_CRF,
        "--songs",
        os.path.expanduser(songs_dir),
        "--size",
        size,
        "--fps",
        str(fps),
        "--out",
        out_path,
        replay_path,
    ]
    if mute:
        args.append("--mute")
    # Written beside the output rather than passed on the command line: a chat's
    # worth of names is longer than an argument list wants to be, and a name can
    # contain anything.
    if leaderboard:
        path = os.path.join(os.path.dirname(out_path) or ".", "rivals.tsv")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(leaderboard)
            args[1:1] = ["--leaderboard", path]
        except OSError as exc:
            logger.warning("could not write the scoreboard: %s", exc)
    # The player's own row is computed by the engine, so its pictures cannot ride
    # in on a line of the file — they come in on their own.
    if leaderboard and all(my_pictures):
        args[1:1] = ["--my-pictures", my_pictures[0], my_pictures[1]]
    if DOSSIER_ENCODER_THREADS.strip():
        args[1:1] = ["--encoder-threads", DOSSIER_ENCODER_THREADS.strip()]

    code, stderr = await _launch_watched(tuple(args), _VIDEO_TIMEOUT_SECONDS, on_progress)
    report = _report_lines(stderr)

    if code != 0:
        # The report, not the raw tail. stderr is almost entirely the progress
        # ticker, so the last 500 characters of it are the last 500 characters
        # of "6600/6849 frames, 70/s, 4s left" — which is what a render tester
        # was shown when a render failed on a server, and it told them nothing.
        # `_report_lines` already drops the ticker; the reason is in what's left.
        said = "\n".join(report[-6:]).strip()
        raise DossierError(said or f"движок завершился с кодом {code} и ничего не сказал")
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise DossierError("движок отработал, но файла нет")

    for line in report:
        logger.info("dossier: %s", line)
    width, height, duration = _video_meta(report)
    return RenderResult(report, width, height, duration)


async def version() -> Optional[str]:
    """Best-effort build identity, for the status line. None when unavailable."""
    if not is_available():
        return None
    try:
        stat = os.stat(binary_path())
    except OSError:
        return None
    return f"{stat.st_size // 1024} KiB, mtime {int(stat.st_mtime)}"
