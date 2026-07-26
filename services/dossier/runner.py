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

from config.settings import DOSSIER_BIN, DOSSIER_SKIN
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


async def _run(
    *args: str,
    timeout: int = _TIMEOUT_SECONDS,
    expect_json: bool = True,
) -> list[dict]:
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

    # A non-zero exit still carries usable JSON — `judge` fails the run when any
    # replay was skipped, but reports every replay it did manage. So parse
    # first and only complain if there's nothing to show.
    results = []
    for line in stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("dossier emitted a non-JSON line: %r", line[:200])

    if not expect_json:
        # `video` writes a file and talks on stderr; there is no JSON to find,
        # so the exit code is the whole of the verdict.
        if process.returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip() or "движок завершился с ошибкой"
            raise DossierError(detail[-500:])
        return []

    if not results:
        detail = stderr.decode("utf-8", "replace").strip() or "движок ничего не вернул"
        raise DossierError(detail[:500])

    return results


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
) -> None:
    """Render the replay to `out_path`.

    Nothing is returned: the engine writes a file and reports progress on
    stderr. Minutes, not seconds — a two-minute map at 720p is around two and a
    half — so this gets its own timeout rather than the one sized for judging.

    The skin comes from settings rather than being fixed here: which look the
    bot renders in is a deployment's decision, not this function's.
    """
    args = [
        "video",
        "--skin",
        skin or DOSSIER_SKIN,
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

    await _run(*args, timeout=_VIDEO_TIMEOUT_SECONDS, expect_json=False)

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise DossierError("движок отработал, но файла нет")


async def version() -> Optional[str]:
    """Best-effort build identity, for the status line. None when unavailable."""
    if not is_available():
        return None
    try:
        stat = os.stat(binary_path())
    except OSError:
        return None
    return f"{stat.st_size // 1024} KiB, mtime {int(stat.st_mtime)}"
