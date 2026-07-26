"""Talking to the `dossier` binary.

Dossier is Rust; the bot is Python. Rather than bind the two together, the bot
runs the binary and reads a line of JSON back. That keeps the engine testable on
its own (`cargo test`, `dossier judge` on a folder of replays) and means a crash
in the simulator is a non-zero exit code, not a dead bot process.
"""

import asyncio
import json
import os
from typing import Optional

from config.settings import DOSSIER_BIN, DOSSIER_CRF, DOSSIER_PRESET, DOSSIER_SKIN
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


async def video(
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    size: str = "1280x720",
    fps: int = 60,
    mute: bool = False,
    skin: str | None = None,
) -> list[str]:
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

    code, _, stderr = await _launch(tuple(args), _VIDEO_TIMEOUT_SECONDS)
    report = _report_lines(stderr)

    if code != 0:
        raise DossierError((stderr.strip() or "движок завершился с ошибкой")[-500:])
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise DossierError("движок отработал, но файла нет")

    for line in report:
        logger.info("dossier: %s", line)
    return report


async def version() -> Optional[str]:
    """Best-effort build identity, for the status line. None when unavailable."""
    if not is_available():
        return None
    try:
        stat = os.stat(binary_path())
    except OSError:
        return None
    return f"{stat.st_size // 1024} KiB, mtime {int(stat.st_mtime)}"
