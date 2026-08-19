"""Asking our own engine what a map demands and what a play was worth.

`dossier assay` is a port of ppy's difficulty and performance calculators,
graded against ppy's own answers on a corpus that lives beside it — exact on the
star rating's inputs, and within seven significant figures of their pp on the
same play. See `dossier/crates/dossier-assay`.

# Why this replaces what it replaces

Two things were wrong with what the bot had, and both are structural rather than
bad luck.

The star rating came from ppy over the network, one map and mod set at a time.
That is exact and it is a round trip, so it is asked for five rows of a card at
a time and cached in memory, and a map nobody has looked at recently costs a
request. This costs a process.

Everything ppy has no endpoint for came from rosu-pp, a third-party port, which
at its newest release disagrees with ppy by up to 0.82 stars. The two figures it
was used for — what a play would have been worth unbroken, and played perfectly
— were shown as estimates anchored to the official pp, because the raw ones were
not good enough to show. They are now computed rather than estimated.

# What is still rosu's

The performance curve on the recent card — `calculate_strains` — needs osu!'s
*gradual* difficulty, which walks a map handing back the rating at every object.
That is not ported, so the curve remains rosu's. It is a shape rather than a
number and nobody reads a value off it, which is the only reason that is
acceptable.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from config.settings import DOSSIER_BIN, PROJECT_ROOT
from utils.logger import get_logger

logger = get_logger("utils.osu.assay")

# Where the `.osu` files handed to the engine are kept.
#
# On disk rather than in memory, unlike the cache this replaces, because the
# engine is a separate process and takes a path. They are small and they never
# change — a beatmap id names one immutable file — so nothing here expires.
CACHE_DIR = Path(
    os.getenv("OSU_FILE_CACHE", os.path.join(PROJECT_ROOT, ".cache", "osu"))
)

# Long enough for a very long map on a busy host, short enough that a hung
# process cannot hold a card hostage.
TIMEOUT_SECONDS = 30.0


def _binary() -> str:
    return os.path.expanduser(DOSSIER_BIN)


async def beatmap_file(beatmap_id: int, download) -> Optional[Path]:
    """The map on disk, downloading it once if it is not there.

    `download` is passed in rather than imported so this module does not care
    where a map comes from — the caller already has an API client with its own
    retries and rate limiting.
    """
    path = CACHE_DIR / f"{int(beatmap_id)}.osu"
    if path.is_file() and path.stat().st_size > 50:
        return path

    body = await download(beatmap_id)
    if not body or len(body) < 50:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Written beside and moved, so a reader never sees a half-written map — two
    # cards for the same beatmap can be drawn at once.
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
    """Everything the engine can say about this map, and this play on it.

    `None` when the engine could not be run or would not answer, which every
    caller has to treat as "show what you can without it" — the binary is built
    separately from the bot and a deployment can be half done.
    """
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
    """The same, starting from a beatmap id."""
    path = await beatmap_file(beatmap_id, download)
    if path is None:
        return None
    return await assay(path, mods, **play)


__all__ = ["assay", "beatmap_file", "for_score", "CACHE_DIR"]
