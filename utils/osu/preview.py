"""Fetch the 10-second beatmap audio preview osu! serves on the web.

`https://b.ppy.sh/preview/{set}.mp3` returns a ~10s clip — but despite the
`.mp3` name it is **OGG/Vorbis**, which Telegram's audio player will not accept.
We transcode it to MP3 with ffmpeg (the clip is tiny, well under a second) so
`sendAudio` renders a proper music card with title/performer.

Results are cached in a tiny LRU keyed by beatmapset_id, since the preview sits
behind a "🔊 Превью" button that the same people may tap repeatedly.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from utils.logger import get_logger

logger = get_logger(__name__)

_PREVIEW_URL = "https://b.ppy.sh/preview/{}.mp3"
_MAX_PREVIEW_BYTES = 4 * 1024 * 1024     # generous; real clips are ~100–300 KB

# set_id -> mp3 bytes (or None = known-missing). Bounded LRU.
_CACHE: "OrderedDict[int, bytes | None]" = OrderedDict()
_CACHE_MAX = 64


def _cache_get(set_id: int):
    if set_id in _CACHE:
        _CACHE.move_to_end(set_id)
        return _CACHE[set_id]
    return _MISS


def _cache_put(set_id: int, value) -> None:
    _CACHE[set_id] = value
    _CACHE.move_to_end(set_id)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


_MISS = object()   # sentinel: not in cache (vs. cached None = no preview)


async def fetch_preview_mp3(beatmapset_id: int) -> bytes | None:
    """Return the set's 10s preview transcoded to MP3, or None if unavailable."""
    set_id = int(beatmapset_id)
    cached = _cache_get(set_id)
    if cached is not _MISS:
        return cached

    ogg = await _download_ogg(set_id)
    mp3 = await _ogg_to_mp3(ogg) if ogg else None
    _cache_put(set_id, mp3)
    return mp3


async def _download_ogg(set_id: int) -> bytes | None:
    import aiohttp
    url = _PREVIEW_URL.format(set_id)
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    logger.info("preview: set %s → HTTP %s", set_id, resp.status)
                    return None
                if resp.content_length and resp.content_length > _MAX_PREVIEW_BYTES:
                    logger.info("preview: set %s preview too large (%s)",
                                set_id, resp.content_length)
                    return None
                data = await resp.read()   # whole body — previews are ~100–300 KB
        if not data or len(data) > _MAX_PREVIEW_BYTES:
            return None
        return data
    except Exception:
        logger.warning("preview: download failed for set %s", set_id, exc_info=True)
        return None


async def _ogg_to_mp3(ogg: bytes) -> bytes | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0", "-vn", "-c:a", "libmp3lame", "-b:a", "128k",
            "-f", "mp3", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("preview: ffmpeg not installed — cannot transcode")
        return None
    try:
        out, err = await asyncio.wait_for(proc.communicate(ogg), timeout=20)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("preview: ffmpeg timed out")
        return None
    if proc.returncode != 0 or not out:
        logger.warning("preview: ffmpeg failed (%s): %s", proc.returncode,
                       (err or b"")[:200].decode("utf-8", "replace"))
        return None
    return out


__all__ = ["fetch_preview_mp3"]
