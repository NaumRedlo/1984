"""Local danser-go renderer for osu! replays.

Requires danser-cli, xvfb-run, ffmpeg installed on the server.
CPU-only rendering via Mesa software (LIBGL_ALWAYS_SOFTWARE=1).
"""

import asyncio
import json
import os
import re
import tempfile
import shutil
from contextlib import asynccontextmanager
from typing import Optional, Dict, Callable, Awaitable

import aiohttp

from utils.logger import get_logger
from config.settings import (
    DANSER_PATH,
    DANSER_SONGS_DIR,
    RENDER_CONCURRENCY,
    RENDER_GPU,
    RENDER_DISPLAY,
    RENDER_GPU_RESOLUTION,
)

logger = get_logger("utils.danser")

# Render at most RENDER_CONCURRENCY at a time (1 on the CPU-only box — software
# GL saturates every core). Extra requests wait FIFO; _inflight counts everyone
# waiting+rendering so callers can show a queue position. Beyond _MAX_QUEUE we
# reject rather than let the backlog grow unbounded.
_render_semaphore = asyncio.Semaphore(RENDER_CONCURRENCY)
_MAX_QUEUE = 10
_inflight = 0

# Beatmap download mirrors (tried in order). chimu.moe is dead; these three are
# the live osz mirrors as of 2026-06.
_BEATMAP_MIRRORS = [
    "https://catboy.best/d/{beatmapset_id}",
    "https://api.osu.direct/d/{beatmapset_id}",
    "https://api.nerinyan.moe/d/{beatmapset_id}",
]

# catboy.best sits behind Cloudflare and 403s aiohttp's default Python UA — send
# a browser User-Agent so the mirror serves the .osz instead of a challenge page.
_DOWNLOAD_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class DanserError(Exception):
    """Raised when danser-cli fails."""
    pass


class DanserNotFoundError(DanserError):
    """Raised when danser-cli binary is not found."""
    pass


class RenderQueueFullError(DanserError):
    """Raised when too many renders are queued."""
    pass


def _check_danser() -> str:
    """Verify danser-cli exists and return its absolute path."""
    path = os.path.expanduser(DANSER_PATH)
    if not os.path.isfile(path):
        raise DanserNotFoundError(
            f"danser-cli не найден по пути: {path}\n"
            "Установите danser-go на сервере."
        )
    if not os.access(path, os.X_OK):
        raise DanserNotFoundError(f"danser-cli не является исполняемым: {path}")
    return path


def _build_spatch(settings: Optional[Dict] = None) -> str:
    """Build -sPatch JSON from user render settings dict.

    danser 0.11.0 schema. danser applies -sPatch AFTER its DB init, so the Songs
    dir here doesn't steer beatmap lookup (DANSER_SONGS_DIR must equal danser's
    on-disk OsuSongsDir) — it's kept only for consistency. We emit ONLY keys
    verified against settings/default.json: a single wrong key can make danser
    drop the whole patch, reverting to its 1080p/CRF14 defaults.

    Two modes:
      * GPU (RENDER_GPU): 1080p60 + NVENC (h264_nvenc) — the A10 rasterizes in
        hardware and NVENC encodes near-instantly. Oversized files are squeezed
        under the cap afterwards by fit_video_to_size.
      * CPU: per-user 720/540 + libx264 CRF 28. The heavy background effects are
        disabled so the software rasterizer (llvmpipe) can keep up; 60 FPS
        doubles the frame count vs 30, so the higher CRF holds the 50 MB cap.
    """
    songs_dir = os.path.expanduser(DANSER_SONGS_DIR)

    if RENDER_GPU:
        fw, fh = 1920, 1080
        if "x" in RENDER_GPU_RESOLUTION:
            w, h = RENDER_GPU_RESOLUTION.split("x", 1)
            fw, fh = int(w), int(h)
        encoder = {
            "Encoder": "h264_nvenc",
            "h264_nvenc": {"RateControl": "cq", "CQ": 24, "Preset": "p7", "Profile": "high"},
        }
    else:
        fw, fh = 1280, 720
        if settings:
            resolution = settings.get("resolution", "1280x720")
            if "x" in resolution:
                w, h = resolution.split("x", 1)
                fw, fh = int(w), int(h)
        encoder = {
            "Encoder": "libx264",
            "libx264": {"RateControl": "crf", "CRF": 28, "Preset": "faster"},
        }

    patch = {
        "General": {"OsuSongsDir": songs_dir},
        "Recording": {
            "FrameWidth": fw,
            "FrameHeight": fh,
            "FPS": 60,
            "Container": "mp4",
            **encoder,
        },
        # Disable the heavy background decorations. Storyboards default ON in
        # 0.11.0 — the rest default off, but we set them explicitly so the render
        # doesn't depend on the on-disk default.json state. Only background
        # eye-candy is touched: cursor, sliders, HUD and scoreboard stay intact.
        "Playfield": {
            "Background": {
                "LoadStoryboards": False,
                "LoadVideos": False,
                "Parallax": {"Enabled": False},
                "Blur": {"Enabled": False},
                "Triangles": {"Enabled": False},
            },
            "Bloom": {"Enabled": False},
        },
    }

    return json.dumps(patch, separators=(",", ":"))


async def download_beatmap(beatmapset_id: int) -> bool:
    """Download a beatmap .osz to danser's Songs directory if not already present.

    Returns True if the map is available (already existed or downloaded).
    """
    songs_dir = os.path.expanduser(DANSER_SONGS_DIR)
    os.makedirs(songs_dir, exist_ok=True)

    # Check if already downloaded (any folder starting with the beatmapset_id)
    for entry in os.listdir(songs_dir):
        if entry.startswith(str(beatmapset_id)):
            return True

    # Download from mirrors
    timeout = aiohttp.ClientTimeout(total=120)
    headers = {"User-Agent": _DOWNLOAD_UA}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for mirror_tpl in _BEATMAP_MIRRORS:
            url = mirror_tpl.format(beatmapset_id=beatmapset_id)
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        logger.debug(f"Mirror {url} returned {resp.status}")
                        continue
                    data = await resp.read()
                    if len(data) < 1000:
                        continue
                    osz_path = os.path.join(songs_dir, f"{beatmapset_id}.osz")
                    with open(osz_path, "wb") as f:
                        f.write(data)
                    logger.info(f"Downloaded beatmap {beatmapset_id} ({len(data)} bytes)")
                    return True
            except Exception as e:
                logger.debug(f"Mirror {url} failed: {e}")
                continue

    logger.warning(f"Failed to download beatmap {beatmapset_id} from all mirrors")
    return False


async def download_replay_file(
    osu_api_client,
    score_id: int,
    output_dir: str,
) -> Optional[str]:
    """Download .osr replay file. Returns path to the file or None."""
    # Try osu! API v2 direct download
    replay_data = None
    try:
        replay_data = await osu_api_client.download_replay(score_id)
    except Exception as e:
        logger.debug(f"API replay download failed for {score_id}: {e}")

    # Fallback: public URL
    if not replay_data:
        try:
            url = f"https://osu.ppy.sh/scores/{score_id}/download"
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status == 200:
                        replay_data = await resp.read()
                        if len(replay_data) < 50:
                            replay_data = None
        except Exception as e:
            logger.debug(f"Public replay download failed for {score_id}: {e}")

    if not replay_data:
        return None

    osr_path = os.path.join(output_dir, f"{score_id}.osr")
    with open(osr_path, "wb") as f:
        f.write(replay_data)
    return osr_path


@asynccontextmanager
async def _render_slot(on_queue: Optional[Callable[[int], Awaitable[None]]]):
    """FIFO admission to a render slot. Counts waiting+rendering jobs so the
    caller can show a queue position, rejects past _MAX_QUEUE, and holds the
    concurrency semaphore for the duration of the body."""
    global _inflight
    if _inflight >= _MAX_QUEUE:
        raise RenderQueueFullError("Очередь рендеров заполнена. Попробуйте позже.")
    _inflight += 1
    try:
        ahead = _inflight - RENDER_CONCURRENCY
        if ahead > 0 and on_queue:
            try:
                await on_queue(ahead)
            except Exception:
                pass
        async with _render_semaphore:
            yield
    finally:
        _inflight -= 1


async def render_replay(
    replay_path: str,
    output_path: str,
    settings: Optional[Dict] = None,
    on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    timeout: Optional[int] = None,
    on_queue: Optional[Callable[[int], Awaitable[None]]] = None,
) -> str:
    """Render a replay to video using danser-cli.

    Args:
        replay_path: Path to .osr file
        output_path: Desired output video path (without extension)
        settings: User render settings dict from DB
        on_progress: Async callback for progress updates
        timeout: Max render time in seconds, or None for no limit (long marathons
            on the CPU-only box can outrun any fixed cap, so default is no limit).
        on_queue: Async callback(position) invoked once if the job must wait for
            renders ahead of it (position = number of jobs ahead in the queue).

    Returns:
        Path to the rendered video file.

    Raises:
        DanserError on failure.
        RenderQueueFullError when the queue is past _MAX_QUEUE.
    """
    danser_path = _check_danser()
    danser_dir = os.path.dirname(danser_path)
    spatch = _build_spatch(settings)

    # Output filename without extension — danser adds .mp4
    out_name = os.path.splitext(os.path.basename(output_path))[0]

    danser_args = [
        danser_path,
        f"-replay={replay_path}",
        "-record",
        f"-out={out_name}",
        "-quickstart",
        "-noupdatecheck",
        "-preciseprogress",
        f"-sPatch={spatch}",
    ]

    env = os.environ.copy()
    if RENDER_GPU:
        # Drive the real GPU-backed Xorg (no Xvfb, no software-GL forcing) so
        # danser rasterizes on the card. The headless Xorg must already be
        # running on RENDER_DISPLAY.
        cmd = danser_args
        env["DISPLAY"] = RENDER_DISPLAY
        env.pop("LIBGL_ALWAYS_SOFTWARE", None)
        env.pop("GALLIUM_DRIVER", None)
    else:
        cmd = ["xvfb-run", "-a", *danser_args]
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        env.setdefault("GALLIUM_DRIVER", "llvmpipe")

    async with _render_slot(on_queue):
        logger.info(f"Starting danser render: {replay_path} -> {out_name}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=danser_dir,
            env=env,
        )

        last_progress = ""
        output_lines = []

        try:
            async def _read_output():
                nonlocal last_progress
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    output_lines.append(text)
                    logger.debug(f"danser: {text}")

                    # Parse progress: "Progress: 42%"
                    match = re.search(r"Progress:\s*(\d+)%", text)
                    if match and on_progress:
                        pct = match.group(1)
                        progress_str = f"Рендеринг: {pct}%"
                        if progress_str != last_progress:
                            last_progress = progress_str
                            try:
                                await on_progress(progress_str)
                            except Exception:
                                pass

            if timeout is None:
                await _read_output()
            else:
                await asyncio.wait_for(_read_output(), timeout=timeout)
            await proc.wait()

        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise DanserError(f"Рендеринг превысил лимит времени ({timeout} сек)")

        if proc.returncode != 0:
            tail = "\n".join(output_lines[-10:])
            logger.error(f"danser exited with code {proc.returncode}:\n{tail}")
            raise DanserError(f"danser завершился с ошибкой (код {proc.returncode})")

    # Find output video — check multiple possible locations
    video_path = None
    search_dirs = [
        os.path.join(danser_dir, "videos"),
        danser_dir,
        os.path.join(danser_dir, "output"),
    ]

    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        candidate = os.path.join(search_dir, f"{out_name}.mp4")
        if os.path.isfile(candidate):
            video_path = candidate
            break
        # Try partial match
        for f in os.listdir(search_dir):
            if f.startswith(out_name) and f.endswith(".mp4"):
                video_path = os.path.join(search_dir, f)
                break
        if video_path:
            break

    # Last resort: search by recent .mp4 in videos dir
    if not video_path:
        videos_dir = os.path.join(danser_dir, "videos")
        if os.path.isdir(videos_dir):
            mp4s = [f for f in os.listdir(videos_dir) if f.endswith(".mp4")]
            if mp4s:
                mp4s.sort(key=lambda f: os.path.getmtime(os.path.join(videos_dir, f)), reverse=True)
                video_path = os.path.join(videos_dir, mp4s[0])
                logger.warning(f"Video not found by name, using most recent: {video_path}")

    if not video_path or not os.path.isfile(video_path):
        # Log diagnostics
        for d in search_dirs:
            if os.path.isdir(d):
                files = os.listdir(d)
                logger.error(f"Files in {d}: {files[:20]}")
        logger.error(f"Expected video name: {out_name}.mp4, danser_dir: {danser_dir}")
        tail = "\n".join(output_lines[-15:])
        logger.error(f"danser output tail:\n{tail}")
        raise DanserError("Видео файл не найден после рендеринга")

    logger.info(f"Render complete: {video_path} ({os.path.getsize(video_path)} bytes)")
    return video_path


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


async def fit_video_to_size(path: str, max_bytes: int, gpu: bool = False) -> str:
    """If the video exceeds max_bytes, re-encode it to a bitrate computed from
    its duration so it fits, and return the new path (the original is removed).
    Otherwise return path unchanged.

    This is how 1080p60 is kept under Telegram's 50 MB cap: short maps keep the
    high-quality first encode, long maps get squeezed to a duration-targeted
    bitrate. NVENC (gpu=True) makes the extra pass near-instant on the A10.
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

    # Budget the bitrate from duration with headroom; reserve audio. 0.88 keeps a
    # margin so a single VBR pass lands under the cap.
    audio_kbps = 128
    total_kbps = (max_bytes * 8 / dur) / 1000.0
    video_kbps = max(int(total_kbps * 0.88 - audio_kbps), 200)
    maxrate = int(video_kbps * 1.3)
    bufsize = int(video_kbps * 2)

    out = f"{os.path.splitext(path)[0]}.fit.mp4"
    if gpu:
        vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr"]
    else:
        vcodec = ["-c:v", "libx264", "-preset", "veryfast"]
    cmd = [
        "ffmpeg", "-y", "-i", path,
        *vcodec,
        "-b:v", f"{video_kbps}k", "-maxrate", f"{maxrate}k", "-bufsize", f"{bufsize}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        out,
    ]
    logger.info(f"fit_video_to_size: {size} > {max_bytes} bytes, re-encoding to ~{video_kbps}k video bitrate")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0 or not os.path.isfile(out):
            logger.error(f"fit re-encode failed: {err.decode('utf-8', 'replace')[-300:]}")
            if os.path.isfile(out):
                os.remove(out)
            return path
        new_size = os.path.getsize(out)
        logger.info(f"fit_video_to_size: {size} -> {new_size} bytes")
        os.remove(path)
        return out
    except Exception as e:
        logger.error(f"fit_video_to_size error: {e}")
        if os.path.isfile(out):
            try:
                os.remove(out)
            except OSError:
                pass
        return path
