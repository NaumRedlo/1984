"""Video post-processing: probe dimensions/duration (ffprobe) and re-encode an
oversized render to fit a byte cap (ffmpeg). Independent of the render path —
the caller probes the finished file and fits it if it overshoots.
"""

import asyncio
import json
import os
from typing import Optional

from utils.logger import get_logger
from config.settings import RENDER_HEVC
from utils.osu.danser_renderer.core import _NVENC_PRESET

logger = get_logger("utils.danser")

_FIT_AUDIO_KBPS = 128
# Aim well under the cap: NVENC VBR overshoots its target by ~15-20%, so 0.82
# usually lands under the cap on the first attempt (the iterative retry is a
# backstop, but each attempt is a full re-encode — expensive on long maps).
_FIT_SAFETY = 0.82
_FIT_MAX_ATTEMPTS = 3


async def probe_video(path: str):
    """Return (width, height, duration_seconds) of a video via ffprobe, or
    (None, None, None) on failure. Telegram renders a video as a square unless
    it's told the real dimensions, so we pass these to answer_video."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration",
            "-of", "json", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        data = json.loads(out.decode("utf-8", "replace") or "{}")
        stream = (data.get("streams") or [{}])[0]
        w = int(stream["width"]) if stream.get("width") else None
        h = int(stream["height"]) if stream.get("height") else None
        dur = (data.get("format") or {}).get("duration")
        d = int(float(dur)) if dur else None
        return w, h, d
    except Exception as e:
        logger.debug(f"ffprobe failed for {path}: {e}")
        return None, None, None


async def _encode_at_bitrate(src: str, out: str, video_kbps: int, gpu: bool) -> bool:
    """Re-encode src to out at the given video bitrate. Returns True on success."""
    maxrate = int(video_kbps * 1.2)
    bufsize = int(video_kbps * 2)
    extra = []
    if gpu and RENDER_HEVC:
        # Speed-first preset (see _NVENC_PRESET) — fit quality matters less than
        # speed, and each attempt re-encodes the whole video.
        vcodec = ["-c:v", "hevc_nvenc", "-preset", _NVENC_PRESET, "-rc", "vbr"]
        extra = ["-tag:v", "hvc1"]  # so players (incl. Telegram/Apple) recognise the HEVC track
    elif gpu:
        vcodec = ["-c:v", "h264_nvenc", "-preset", _NVENC_PRESET, "-rc", "vbr"]
    else:
        vcodec = ["-c:v", "libx264", "-preset", "veryfast"]
    cmd = [
        "ffmpeg", "-y", "-i", src,
        *vcodec,
        "-b:v", f"{video_kbps}k", "-maxrate", f"{maxrate}k", "-bufsize", f"{bufsize}k",
        *extra,
        "-c:a", "aac", "-b:a", f"{_FIT_AUDIO_KBPS}k",
        "-movflags", "+faststart",
        out,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not os.path.isfile(out):
        logger.error(f"fit re-encode failed: {err.decode('utf-8', 'replace')[-300:]}")
        return False
    return True


async def fit_video_to_size(path: str, max_bytes: int, gpu: bool = False) -> str:
    """If the video exceeds max_bytes, re-encode it to fit and return the new path
    (the original is removed). Otherwise return path unchanged.

    Single-pass NVENC VBR overshoots its target bitrate badly at low bitrates, so
    this aims under the cap and, if the result is still over, scales the bitrate
    down by the observed ratio and re-encodes — up to _FIT_MAX_ATTEMPTS times.
    Each attempt re-encodes from the original to avoid compounding quality loss.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return path
    if max_bytes <= 0 or size <= max_bytes:
        return path

    _, _, dur = await probe_video(path)
    if not dur or dur <= 0:
        logger.warning(f"fit_video_to_size: no duration for {path}, leaving as is")
        return path

    target_bytes = int(max_bytes * _FIT_SAFETY)
    video_kbps = max(int((target_bytes * 8 / dur) / 1000.0) - _FIT_AUDIO_KBPS, 200)

    out = f"{os.path.splitext(path)[0]}.fit.mp4"
    best: Optional[str] = None  # smallest produced so far (under cap if we got there)

    for attempt in range(_FIT_MAX_ATTEMPTS):
        logger.info(
            f"fit_video_to_size: {size} > {max_bytes} bytes, attempt {attempt + 1} "
            f"@ ~{video_kbps}k video bitrate"
        )
        if not await _encode_at_bitrate(path, out, video_kbps, gpu):
            break
        new_size = os.path.getsize(out)
        logger.info(f"fit_video_to_size: -> {new_size} bytes (attempt {attempt + 1})")
        if new_size <= max_bytes:
            os.remove(path)
            return out
        # Overshot: keep this as best-effort, then scale the bitrate down by the
        # observed ratio (with a little extra) and try again.
        if best:
            try:
                os.remove(best)
            except OSError:
                pass
        best = f"{os.path.splitext(path)[0]}.fit{attempt}.mp4"
        os.replace(out, best)
        video_kbps = max(int(video_kbps * (target_bytes / new_size)), 200)

    # No attempt landed under the cap. Return the smallest re-encode if we have
    # one (still better than the original); otherwise the original untouched.
    if best and os.path.isfile(best):
        new_size = os.path.getsize(best)
        logger.warning(f"fit_video_to_size: best effort {new_size} bytes still over {max_bytes}")
        if new_size < size:
            os.remove(path)
            return best
        os.remove(best)
    if os.path.isfile(out):
        try:
            os.remove(out)
        except OSError:
            pass
    return path
