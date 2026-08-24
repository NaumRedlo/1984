import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from config.settings import DOSSIER_BIN, PROJECT_ROOT
from utils.logger import get_logger

logger = get_logger("utils.osu.assay")

CACHE_DIR = Path(
    os.getenv("OSU_FILE_CACHE", os.path.join(PROJECT_ROOT, ".cache", "osu"))
)

TIMEOUT_SECONDS = 30.0


def _binary() -> str:
    return os.path.expanduser(DOSSIER_BIN)


async def beatmap_file(beatmap_id: int, download) -> Optional[Path]:
    path = CACHE_DIR / f"{int(beatmap_id)}.osu"
    if path.is_file() and path.stat().st_size > 50:
        return path

    body = await download(beatmap_id)
    if not body or len(body) < 50:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(f".{hashlib.sha1(body[:64]).hexdigest()[:8]}.part")
    scratch.write_bytes(body)
    scratch.replace(path)
    return path


async def assay(
    path: Path,
    mods: str = "",
    *,
    accuracy: Optional[float] = None,
    combo: Optional[int] = None,
    misses: int = 0,
    count_300: Optional[int] = None,
    count_100: Optional[int] = None,
    count_50: Optional[int] = None,
    slider_ends: Optional[int] = None,
    large_tick_misses: int = 0,
    classic: bool = False,
    legacy_total: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    args = [_binary(), "assay", "--map", str(path)]
    if mods:
        args += ["--mods", mods]
    for flag, value in (
        ("--accuracy", accuracy),
        ("--combo", combo),
        ("--misses", misses or None),
        ("--n300", count_300),
        ("--n100", count_100),
        ("--n50", count_50),
        ("--slider-ends", slider_ends),
        ("--large-tick-misses", large_tick_misses or None),
        ("--legacy-total", legacy_total),
    ):
        if value is not None:
            args += [flag, str(value)]
    if classic:
        args.append("--classic")

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(process.communicate(), TIMEOUT_SECONDS)
    except FileNotFoundError:
        logger.warning("assay: no engine at %s", _binary())
        return None
    except asyncio.TimeoutError:
        logger.warning("assay: engine did not answer within %.0fs", TIMEOUT_SECONDS)
        return None
    except Exception:  # noqa: BLE001 — a card is worth more than a stack trace
        logger.warning("assay: engine could not be run", exc_info=True)
        return None

    if process.returncode != 0:
        logger.warning("assay: engine said %s", (err or b"").decode()[:300])
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        logger.warning("assay: engine answered something that is not JSON")
        return None


async def for_score(
    beatmap_id: int,
    download,
    mods: str = "",
    **play,
) -> Optional[dict[str, Any]]:
    path = await beatmap_file(beatmap_id, download)
    if path is None:
        return None
    return await assay(path, mods, **play)


__all__ = ["assay", "beatmap_file", "for_score", "CACHE_DIR"]
