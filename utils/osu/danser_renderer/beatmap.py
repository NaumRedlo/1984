"""Beatmap (.osz) acquisition for danser.

Fetches from the mirror(s) with retries, validates the payload is a real zip
(not an HTML error page), and writes it to danser's Songs dir. In remote-worker
mode the BOT calls fetch_beatmap_osz and hands the bytes to the worker via
save_beatmap_osz — see fetch_beatmap_osz's docstring for why.
"""

import asyncio
import os

import requests

from utils.logger import get_logger
from config.settings import DANSER_SONGS_DIR

logger = get_logger("utils.danser")

# 2026-07-03 incident: download_beatmap() failed on all 3 mirrors for a real,
# available set (2539465) shortly after a fresh worker boot — the aggregate
# "failed from all mirrors" WARNING gave no way to tell which mirror(s) were
# actually at fault (per-mirror attempts only logged at DEBUG). Narrowed to
# osu.direct alone, deliberately, as a diagnostic experiment: with a single
# mirror, any future failure is unambiguous, and _DOWNLOAD_RETRIES below gives
# it its own resilience now that there's no second/third mirror to fall back
# on. catboy.best/beatconnect.io are dropped for now, not because they're bad
# (catboy.best was "rock-solid" per the prior note) — just to isolate the
# variable. Re-add them if osu.direct alone proves unreliable.
_BEATMAP_MIRRORS = [
    "https://osu.direct/d/{beatmapset_id}",
]

# Retries for the single mirror above (short backoff) — losing the other two
# mirrors as fallbacks means a bare transient failure (e.g. network still
# settling right after a cold VM boot, per this same incident) would otherwise
# have zero resilience left.
_DOWNLOAD_RETRIES = 3
_DOWNLOAD_RETRY_SECONDS = 2.0

# catboy.best sits behind Cloudflare and 403s aiohttp's default Python UA — send
# a browser User-Agent so the mirror serves the .osz instead of a challenge page.
_DOWNLOAD_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _beatmap_already_present(beatmapset_id: int) -> bool:
    songs_dir = os.path.expanduser(DANSER_SONGS_DIR)
    os.makedirs(songs_dir, exist_ok=True)
    return any(e.startswith(str(beatmapset_id)) for e in os.listdir(songs_dir))


async def fetch_beatmap_osz(beatmapset_id: int):
    """Fetch a beatmap .osz from the mirror(s), validated (real zip, not a
    small HTML error/landing page), with retries. Pure fetch — does not touch
    disk or check whether the map is already present; callers that want the
    file on THIS machine's Songs dir should use download_beatmap() instead,
    which wraps this. Returns None if every attempt failed.

    2026-07-04: this is what the BOT calls directly in remote-worker mode
    (see render.py's callers) instead of letting the worker fetch it itself —
    the worker's outbound internet goes through a bandwidth-limited proxy
    (Squid delay pools: fast burst, then throttled to a crawl) that a file
    this size never finishes downloading through. The bot's own connection
    isn't behind that proxy, so it fetches the bytes and hands them to the
    worker over their existing (already proven, unthrottled) channel instead
    — see save_beatmap_osz().
    """
    # Retrying the whole pass a few times — with only one mirror left (see
    # _BEATMAP_MIRRORS' note) there's no second mirror to fall back on, so a
    # transient failure needs its own resilience here. Per-attempt outcomes
    # are logged at INFO (was DEBUG) so a future "failed from all mirrors" is
    # diagnosable straight from the normal-level logs, not just the final
    # aggregate WARNING.
    #
    # 2026-07-03: uses requests (blocking, run off-thread via asyncio.to_thread)
    # — NOT aiohttp, NOT httpx. On the worker, this used to matter because its
    # outbound internet is proxied (http(s)_proxy env vars, required — direct
    # connections don't reach the internet at all) and both async HTTP clients
    # failed tunneling HTTPS through this proxy's CONNECT tunnel: aiohttp
    # doesn't read proxy env vars without trust_env=True, and even with that
    # set, fails fast with a confirmed still-open bug (aio-libs/aiohttp#8469);
    # httpx (which DOES read them by default) got further but died mid-TLS-
    # handshake with SSLEOFError — both ultimately go through an event loop's
    # start_tls() to upgrade an already-CONNECTed socket to TLS, which is the
    # fragile part. `curl` and `requests` do this the traditional blocking way
    # (wrap_socket on an already-tunneled socket, no event loop involved).
    # Kept even now that the BOT is the one calling this (not behind that
    # proxy) for consistency — no reason to use a different client here.
    headers = {"User-Agent": _DOWNLOAD_UA}

    def _sync_get(url: str):
        resp = requests.get(url, headers=headers, timeout=120.0, allow_redirects=True)
        return resp.status_code, resp.content

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
    """Download a beatmap .osz to danser's Songs directory if not already
    present. Returns True if the map is available (already existed or
    downloaded). Local-mode / fallback path — see fetch_beatmap_osz's note
    for why remote mode now avoids calling this on the worker."""
    if _beatmap_already_present(beatmapset_id):
        return True
    data = await fetch_beatmap_osz(beatmapset_id)
    if data is None:
        return False
    songs_dir = os.path.expanduser(DANSER_SONGS_DIR)
    osz_path = os.path.join(songs_dir, f"{beatmapset_id}.osz")
    with open(osz_path, "wb") as f:
        f.write(data)
    return True


def save_beatmap_osz(beatmapset_id: int, osz_bytes: bytes) -> bool:
    """Write beatmap bytes the caller already fetched straight to disk — no
    network involved. Used by the render worker when the bot has already
    downloaded the .osz itself and is handing it over directly (see
    fetch_beatmap_osz's note). Returns False if the bytes don't look like a
    real .osz (caller should treat this the same as a failed download)."""
    if _beatmap_already_present(beatmapset_id):
        return True
    if len(osz_bytes) < 1000 or osz_bytes[:2] != b"PK":
        return False
    songs_dir = os.path.expanduser(DANSER_SONGS_DIR)
    osz_path = os.path.join(songs_dir, f"{beatmapset_id}.osz")
    with open(osz_path, "wb") as f:
        f.write(osz_bytes)
    logger.info(f"Saved beatmap {beatmapset_id} from bot-provided bytes ({len(osz_bytes)} bytes)")
    return True
