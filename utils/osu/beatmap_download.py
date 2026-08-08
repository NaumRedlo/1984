"""Beatmap (.osz) acquisition.

Fetches a beatmapset from the mirror(s) with retries, validates the payload is a
real zip (not an HTML error page), and can write it to the local beatmap store.

Kept as a standalone utility after the replay renderer was removed — it's the
bot's general-purpose "get me this map's .osz" path and is expected to be reused.
"""

import asyncio
import os

import requests

from utils.logger import get_logger
from config.settings import BEATMAP_STORE_DIR

logger = get_logger("utils.beatmap")

# 2026-07-03 incident: download_beatmap() failed on all 3 mirrors for a real,
# available set (2539465) shortly after a fresh boot — the aggregate "failed
# from all mirrors" WARNING gave no way to tell which mirror(s) were actually
# at fault (per-mirror attempts only logged at DEBUG). Narrowed to osu.direct
# alone, deliberately, as a diagnostic experiment: with a single mirror, any
# future failure is unambiguous.
#
# 2026-07-30: that experiment is over and its answer is unambiguous. Measured
# against a real set, osu.direct answered **HTTP 522** — Cloudflare could not
# reach it at all — while catboy.best, nerinyan and beatconnect each returned a
# valid .osz with its audio. So the one mirror we had narrowed down to was the
# one that was down, and maps stopped arriving for exactly that reason.
#
# The fallbacks are back, ordered by what that measurement found. What the
# experiment was *for* is kept: every per-mirror outcome is logged with its
# status, so "failed from all mirrors" is still attributable to a mirror rather
# than to the idea of mirrors. Diagnosability did not need the single point of
# failure; it needed the logging, and the logging is what stays.
#
# osu.direct is kept last rather than dropped — a mirror behind a 522 today is a
# mirror that may be fine tomorrow, and it costs nothing at the end of a list
# that has already succeeded.
_BEATMAP_MIRRORS = [
    "https://catboy.best/d/{beatmapset_id}",
    "https://api.nerinyan.moe/d/{beatmapset_id}",
    "https://beatconnect.io/b/{beatmapset_id}",
    "https://osu.direct/d/{beatmapset_id}",
]

# Passes over the whole list, with a short backoff between them. Four mirrors
# make a single bad answer cheap; the retries are for the case the incident above
# was really about — a host whose network is still settling right after a boot,
# where every mirror fails at once and none of them is at fault.
_DOWNLOAD_RETRIES = 3
_DOWNLOAD_RETRY_SECONDS = 2.0

# The most an .osz may be before we stop reading it. A mirror is a third party,
# and `requests` reads a whole response into memory: without a ceiling, a mirror
# that answered a set-download with a multi-gigabyte body — broken, hostile, or
# a redirect gone wrong — would be the bot's memory, on a host where the corpus
# tool already caps this very download at the same figure. Larger than any real
# beatmapset (the biggest marathons are tens of MB), so nothing real is refused.
_MAX_OSZ_BYTES = 200 * 1024 * 1024

# Mirrors behind Cloudflare 403 aiohttp's default Python UA — send a browser
# User-Agent so the mirror serves the .osz instead of a challenge page.
_DOWNLOAD_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _beatmap_already_present(beatmapset_id: int) -> bool:
    songs_dir = os.path.expanduser(BEATMAP_STORE_DIR)
    os.makedirs(songs_dir, exist_ok=True)
    return any(e.startswith(str(beatmapset_id)) for e in os.listdir(songs_dir))


async def fetch_beatmap_osz(beatmapset_id: int):
    """Fetch a beatmap .osz from the mirror(s), validated (real zip, not a
    small HTML error/landing page), with retries. Pure fetch — does not touch
    disk or check whether the map is already present; callers that want the
    file on disk should use download_beatmap() instead, which wraps this.
    Returns None if every attempt failed.
    """
    # Each mirror in turn, then the whole list again after a backoff. Per-attempt
    # outcomes are logged at INFO (was DEBUG) so a future "failed from all
    # mirrors" is diagnosable straight from the normal-level logs, and names the
    # mirror — which is the whole reason the 2026-07-03 narrowing happened and
    # the part of it worth keeping.
    #
    # 2026-07-03: uses requests (blocking, run off-thread via asyncio.to_thread)
    # — NOT aiohttp, NOT httpx. This mattered on a host whose outbound internet
    # was proxied (http(s)_proxy env vars, required — direct connections didn't
    # reach the internet at all): both async HTTP clients failed tunneling
    # HTTPS through that proxy's CONNECT tunnel. aiohttp doesn't read proxy env
    # vars without trust_env=True, and even with that set fails fast with a
    # confirmed still-open bug (aio-libs/aiohttp#8469); httpx (which DOES read
    # them by default) got further but died mid-TLS-handshake with SSLEOFError
    # — both ultimately go through an event loop's start_tls() to upgrade an
    # already-CONNECTed socket to TLS, which is the fragile part. `curl` and
    # `requests` do this the traditional blocking way (wrap_socket on an
    # already-tunneled socket, no event loop involved). Kept for that
    # resilience — no reason to risk a different client here.
    headers = {"User-Agent": _DOWNLOAD_UA}

    def _sync_get(url: str):
        # Streamed and read up to a ceiling rather than taken whole, so a mirror
        # cannot decide how much of this machine's memory to use. Past the cap
        # the download is abandoned and reported as too large, and the caller
        # falls through to the next mirror as it would for any other failure.
        with requests.get(
            url, headers=headers, timeout=120.0, allow_redirects=True, stream=True
        ) as resp:
            if resp.status_code != 200:
                return resp.status_code, b""
            data = bytearray()
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                data.extend(chunk)
                if len(data) > _MAX_OSZ_BYTES:
                    raise ValueError(f"over {_MAX_OSZ_BYTES // (1024 * 1024)}MB")
            return resp.status_code, bytes(data)

    for attempt in range(1, _DOWNLOAD_RETRIES + 1):
        for mirror_tpl in _BEATMAP_MIRRORS:
            url = mirror_tpl.format(beatmapset_id=beatmapset_id)
            try:
                status, data = await asyncio.to_thread(_sync_get, url)
                if status != 200:
                    logger.info(f"Mirror {url} returned {status} (attempt {attempt}/{_DOWNLOAD_RETRIES})")
                    continue
                # An .osz is a zip — must start with "PK". Some mirrors answer
                # 200 with a small HTML landing/error page when a set is missing;
                # reject that so we don't save a corrupt map and fall through to
                # the next mirror.
                if len(data) < 1000 or data[:2] != b"PK":
                    logger.info(f"Mirror {url} returned non-osz ({len(data)}b, attempt {attempt}/{_DOWNLOAD_RETRIES})")
                    continue
                logger.info(f"Fetched beatmap {beatmapset_id} ({len(data)} bytes)")
                return data
            except Exception as e:
                logger.info(f"Mirror {url} failed (attempt {attempt}/{_DOWNLOAD_RETRIES}): {e}")
                continue
        if attempt < _DOWNLOAD_RETRIES:
            await asyncio.sleep(_DOWNLOAD_RETRY_SECONDS)

    logger.warning(f"Failed to download beatmap {beatmapset_id} from all mirrors after {_DOWNLOAD_RETRIES} attempts")
    return None


async def download_beatmap(beatmapset_id: int) -> bool:
    """Download a beatmap .osz into the local store if not already present.
    Returns True if the map is available (already existed or downloaded)."""
    if _beatmap_already_present(beatmapset_id):
        return True
    data = await fetch_beatmap_osz(beatmapset_id)
    if data is None:
        return False
    songs_dir = os.path.expanduser(BEATMAP_STORE_DIR)
    osz_path = os.path.join(songs_dir, f"{beatmapset_id}.osz")
    with open(osz_path, "wb") as f:
        f.write(data)
    return True


def save_beatmap_osz(beatmapset_id: int, osz_bytes: bytes) -> bool:
    """Write beatmap bytes the caller already fetched straight to disk — no
    network involved. Returns False if the bytes don't look like a real .osz
    (caller should treat this the same as a failed download)."""
    if _beatmap_already_present(beatmapset_id):
        return True
    if len(osz_bytes) < 1000 or osz_bytes[:2] != b"PK":
        return False
    songs_dir = os.path.expanduser(BEATMAP_STORE_DIR)
    osz_path = os.path.join(songs_dir, f"{beatmapset_id}.osz")
    with open(osz_path, "wb") as f:
        f.write(osz_bytes)
    logger.info(f"Saved beatmap {beatmapset_id} from bot-provided bytes ({len(osz_bytes)} bytes)")
    return True
