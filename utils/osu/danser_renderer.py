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
from typing import Optional, Dict, Callable, Awaitable

import aiohttp

from utils.logger import get_logger
from config.settings import DANSER_PATH, DANSER_SONGS_DIR

logger = get_logger("utils.danser")

# Limit concurrent renders to avoid overloading CPU
_render_semaphore = asyncio.Semaphore(2)

# Beatmap download mirrors (tried in order)
_BEATMAP_MIRRORS = [
    "https://catboy.best/d/{beatmapset_id}",
    "https://api.chimu.moe/v1/download/{beatmapset_id}",
]


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
    """Build -sPatch JSON from user render settings dict."""
    songs_dir = os.path.expanduser(DANSER_SONGS_DIR)
    patch = {
        "General": {
            "OsuSongsDir": songs_dir,
        },
        # Fixed CPU-optimized recording settings
        "Recording": {
            "FPS": 60,
            "Encoder": "libx264",
            "Container": "mp4",
            "X264": {
                "Preset": "fast",
                "CRF": 23,
                "RateControl": "CRF",
            },
        },
    }

    if not settings:
        return json.dumps(patch, separators=(",", ":"))

    # Skin
    skin = settings.get("skin", "default")
    patch["Skin"] = {
        "CurrentSkin": skin,
        "Cursor": {
            "UseSkinCursor": True,
            "Scale": settings.get("cursor_size", 1.0),
            "ForceLongTrail": settings.get("cursor_trail", True),
        },
    }

    # Resolution
    resolution = settings.get("resolution", "1280x720")
    if "x" in resolution:
        w, h = resolution.split("x", 1)
        patch["Recording"]["FrameWidth"] = int(w)
        patch["Recording"]["FrameHeight"] = int(h)

    # Gameplay elements
    patch["Gameplay"] = {
        "PPCounter": {"Show": settings.get("show_pp_counter", True)},
        "KeyOverlay": {"Show": settings.get("show_key_overlay", True)},
        "HitErrorMeter": {"Show": settings.get("show_hit_error_meter", True)},
        "Mods": {"HideInReplays": not settings.get("show_mods", True)},
        "Score": {"Show": True},
        "HpBar": {"Show": True},
        "ComboCounter": {"Show": True},
        "ResultsScreen": {
            "ShowResultsScreen": settings.get("show_result_screen", True),
        },
    }

    # Scoreboard
    if settings.get("show_scoreboard", False):
        patch["Gameplay"]["ScoreBoard"] = {"Mode": "Normal"}

    # Background dim (0-100 int → 0.0-1.0 float)
    bg_dim = settings.get("bg_dim", 80)
    patch["Playfield"] = {
        "Background": {
            "Dim": {
                "Normal": bg_dim / 100.0,
                "Breaks": max(0, bg_dim / 100.0 - 0.2),
            }
        }
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
    async with aiohttp.ClientSession(timeout=timeout) as session:
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


async def render_replay(
    replay_path: str,
    output_path: str,
    settings: Optional[Dict] = None,
    on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    timeout: int = 600,
) -> str:
    """Render a replay to video using danser-cli.

    Args:
        replay_path: Path to .osr file
        output_path: Desired output video path (without extension)
        settings: User render settings dict from DB
        on_progress: Async callback for progress updates
        timeout: Max render time in seconds

    Returns:
        Path to the rendered video file.

    Raises:
        DanserError on failure.
        RenderQueueFullError if too many concurrent renders.
    """
    danser_path = _check_danser()
    danser_dir = os.path.dirname(danser_path)
    spatch = _build_spatch(settings)

    # Output filename without extension — danser adds .mp4
    out_name = os.path.splitext(os.path.basename(output_path))[0]

    cmd = [
        "xvfb-run", "-a",
        danser_path,
        f"-replay={replay_path}",
        "-record",
        f"-out={out_name}",
        "-quickstart",
        "-preciseprogress",
        f"-sPatch={spatch}",
    ]

    env = os.environ.copy()
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"

    # Try to acquire semaphore without blocking
    if _render_semaphore.locked() and _render_semaphore._value == 0:
        raise RenderQueueFullError("Слишком много рендеров в очереди. Попробуйте позже.")

    async with _render_semaphore:
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
