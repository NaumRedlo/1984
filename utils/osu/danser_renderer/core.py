"""Core danser render path: GL/binary readiness checks, the -sPatch settings
builder, FIFO queue admission, and the render_replay subprocess driver, plus the
replay download. This is where the render/queue/GL module state lives.
"""

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from typing import Optional, Dict, Callable, Awaitable

import requests

from utils.logger import get_logger
from config.settings import (
    DANSER_PATH,
    DANSER_SONGS_DIR,
    RENDER_CONCURRENCY,
    RENDER_GPU,
    RENDER_DISPLAY,
    RENDER_GPU_RESOLUTION,
    RENDER_HEVC,
    RENDER_NVENC_PRESET,
    RENDER_FIT_MAX_MB,
)
from utils.osu.danser_renderer.errors import (
    DanserError, DanserNotFoundError, RenderQueueFullError,
)

logger = get_logger("utils.danser")

# NVENC preset for both the main render and the fit re-encode. p7 (slowest) made
# the A10's encoder the bottleneck (enc 100% / sm 12%); p4 unsticks it.
_NVENC_PRESET = RENDER_NVENC_PRESET

# Single-pass sizing: when the map length is known we render straight to a target
# bitrate that lands under the cap, so the encoder runs ONCE instead of CQ + a
# second fit re-encode (the encoder is the bottleneck, so a whole extra pass is
# the costliest thing we can cut). fit_video_to_size stays as a backstop for the
# rare overshoot (NVENC VBR has no maxrate here). 0.85 leaves headroom for that.
_AUDIO_KBPS = 128
_SINGLE_PASS_SAFETY = 0.85


def _target_video_kbps(length_seconds: float) -> int:
    """Video bitrate (kbps) so length_seconds of video + audio fits the cap."""
    cap_bytes = RENDER_FIT_MAX_MB * 1024 * 1024
    target = int(cap_bytes * _SINGLE_PASS_SAFETY)
    kbps = int((target * 8 / length_seconds) / 1000) - _AUDIO_KBPS
    return max(kbps, 500)


# Render at most RENDER_CONCURRENCY at a time (1 on the CPU-only box — software
# GL saturates every core). Extra requests wait FIFO; _inflight counts everyone
# waiting+rendering so callers can show a queue position. Beyond _MAX_QUEUE we
# reject rather than let the backlog grow unbounded.
_render_semaphore = asyncio.Semaphore(RENDER_CONCURRENCY)
_MAX_QUEUE = 10
_inflight = 0

# 2026-07-03 incident: a render right after a fresh GPU wake (VM cold boot)
# hit danser's known GL-context deadlock (framework/goroutines.CallMain stuck
# on a channel receive — the main GL loop never started, so nothing ever
# drained the queue). Root cause: the worker's /health only confirmed the
# Python process was listening, which happens within seconds of boot — long
# before Xorg's NVIDIA driver stack has actually finished settling enough to
# reliably hand out a GLX context (nvidia-xorg logged "Started" 3s before our
# health check passed, but danser still deadlocked 2.5 minutes later). glxinfo
# does a REAL GLX context creation+query, the same operation danser's startup
# needs — so a clean glxinfo run is a much more honest readiness signal than
# "the socket accepted a connection". Cached true forever once confirmed (GL
# readiness doesn't regress without this process itself restarting, which
# resets the module-level flag anyway) so the cost is paid once per boot, not
# on every health poll.
_gl_ready_confirmed = False
_glxinfo_missing_warned = False


async def _check_gl_ready() -> bool:
    """Best-effort GLX readiness probe for RENDER_GPU mode. Missing glxinfo
    degrades to "assume ready" (this is a defense-in-depth addition, not a
    hard new dependency that should be able to wedge the wake loop forever)."""
    global _gl_ready_confirmed, _glxinfo_missing_warned
    if _gl_ready_confirmed or not RENDER_GPU:
        return True
    env = os.environ.copy()
    env["DISPLAY"] = RENDER_DISPLAY
    try:
        proc = await asyncio.create_subprocess_exec(
            "glxinfo", "-B",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        returncode = await asyncio.wait_for(proc.wait(), timeout=5.0)
    except FileNotFoundError:
        if not _glxinfo_missing_warned:
            logger.warning("glxinfo not found — can't verify GL readiness before rendering, assuming ready")
            _glxinfo_missing_warned = True
        _gl_ready_confirmed = True
        return True
    except asyncio.TimeoutError:
        logger.warning("glxinfo hung past 5s — GLX likely not ready yet")
        return False
    if returncode == 0:
        _gl_ready_confirmed = True
        return True
    logger.info(f"glxinfo exited {returncode} — GLX not ready yet")
    return False


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

    # Resolution: honour the user's choice (the /settings menu) in both modes,
    # falling back to the mode default. CPU is clamped to 720p — 1080p on llvmpipe
    # is impractically slow on the bot box.
    default_res = RENDER_GPU_RESOLUTION if RENDER_GPU else "1280x720"
    res = default_res
    if settings and "x" in str(settings.get("resolution") or ""):
        res = settings["resolution"]
    try:
        w, h = res.split("x", 1)
        fw, fh = int(w), int(h)
    except (ValueError, AttributeError):
        fw, fh = 1280, 720
    if not RENDER_GPU and fh > 720:
        fw, fh = 1280, 720

    # If we know the playback length (passed in as length_seconds), render the GPU
    # pass straight to a size-targeted bitrate (single pass) instead of CQ — that
    # skips the second fit re-encode for almost all maps. Without a length, or in
    # CPU mode, keep the quality-targeted CQ/CRF path.
    single_pass_kbps = None
    if RENDER_GPU and RENDER_FIT_MAX_MB > 0 and settings:
        length = settings.get("length_seconds")
        if length and float(length) > 0:
            single_pass_kbps = _target_video_kbps(float(length))

    if RENDER_GPU and RENDER_HEVC:
        if single_pass_kbps:
            nvenc = {"RateControl": "vbr", "Bitrate": f"{single_pass_kbps}k", "Preset": _NVENC_PRESET}
        else:
            nvenc = {"RateControl": "cq", "CQ": 26, "Preset": _NVENC_PRESET}
        encoder = {"Encoder": "hevc_nvenc", "hevc_nvenc": nvenc}
    elif RENDER_GPU:
        if single_pass_kbps:
            nvenc = {"RateControl": "vbr", "Bitrate": f"{single_pass_kbps}k", "Preset": _NVENC_PRESET, "Profile": "high"}
        else:
            nvenc = {"RateControl": "cq", "CQ": 24, "Preset": _NVENC_PRESET, "Profile": "high"}
        encoder = {"Encoder": "h264_nvenc", "h264_nvenc": nvenc}
    else:
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

    # Per-user HUD / dim / cursor from the /settings menu. Keys verified against
    # the 0.11.0 default.json — a wrong key drops the whole patch, so only these.
    if settings:
        # Cinema mode: hide the ENTIRE HUD (map + cursor only), overriding the
        # individual toggles. All keys verified against 0.11.0 default.json.
        cinema = bool(settings.get("cinema_mode"))

        def _show(key, default):
            return False if cinema else bool(settings.get(key, default))

        patch["Gameplay"] = {
            "PPCounter": {"Show": _show("show_pp_counter", True)},
            "ScoreBoard": {"Show": _show("show_scoreboard", False)},
            "KeyOverlay": {"Show": _show("show_key_overlay", True)},
            "HitErrorMeter": {"Show": _show("show_hit_error_meter", True)},
            "Mods": {"Show": _show("show_mods", True)},
            "StrainGraph": {"Show": _show("show_strain_graph", True)},
            "HitCounter": {"Show": _show("show_hit_counter", True)},
            "HpBar": {"Show": _show("show_hp_bar", True)},
            "ShowResultsScreen": (False if cinema else bool(settings.get("show_result_screen", True))),
            # The playfield outline ("очерчение игровой зоны") is danser's own
            # overlay, not part of the skin — drop it for a clean clip.
            "Boundaries": {"Enabled": False},
        }
        # Score element = score + accuracy + grade + progress/time (ONE widget).
        # danser shows it by default, so only emit the key when HIDING — keeps this
        # one less-battle-tested key off the default path (a wrong key drops the
        # whole patch), limiting any risk to cinema / score-off users.
        if cinema or not bool(settings.get("show_score", True)):
            patch["Gameplay"]["Score"] = {"Show": False}
        if cinema:
            # Elements without their own toggle — hide for the map-only view.
            patch["Gameplay"]["ComboCounter"] = {"Show": False}
            patch["Gameplay"]["AimErrorMeter"] = {"Show": False}

        # Background: cinema shows the storyboard/video at a fixed 80% dim (an
        # immersive "watch the map" view); otherwise honour the user's dim and keep
        # storyboards/videos off (perf).
        if cinema:
            patch["Playfield"]["Background"]["LoadStoryboards"] = True
            patch["Playfield"]["Background"]["LoadVideos"] = True
            patch["Playfield"]["Background"]["Dim"] = {"Normal": 0.8}
        else:
            dim = settings.get("bg_dim")
            if dim is not None:
                patch["Playfield"]["Background"]["Dim"] = {
                    "Normal": max(0, min(100, int(dim))) / 100.0,
                }
        patch["Playfield"]["SeizureWarning"] = {
            "Enabled": bool(settings.get("show_seizure_warning", False)),
        }

        # Audio: master at full so the % sliders map directly; per-user music /
        # hitsound volume; IgnoreBeatmapSamples=true plays the SKIN's hitsounds
        # instead of the beatmap's. (danser defaults are 0.5 each = quiet clips.)
        def _vol(key, default=100):
            return max(0, min(100, int(settings.get(key, default)))) / 100.0
        patch["Audio"] = {
            "GeneralVolume": 1.0,
            "MusicVolume": _vol("music_volume"),
            "SampleVolume": _vol("hitsound_volume"),
            "IgnoreBeatmapSamples": bool(settings.get("use_skin_hitsounds", False)),
        }

        skin = settings.get("skin") or "default"
        cursor = settings.get("cursor_size")
        patch["Skin"] = {"CurrentSkin": str(skin)}
        if skin != "default":
            # danser ignores a skin's cursor and colours unless told to use them;
            # otherwise it draws its own (rainbow) cursor/colours over the skin.
            patch["Skin"]["UseColorsFromSkin"] = True
            patch["Skin"]["Cursor"] = {"UseSkinCursor": True}
            if cursor:
                patch["Skin"]["Cursor"]["Scale"] = float(cursor)
            patch["Objects"] = {"Colors": {"UseSkinComboColors": True}}
        elif cursor:
            # Default skin -> danser's own cursor, sized by Cursor.CursorSize
            # (base 12), NOT Skin.Cursor.Scale (which only scales a skin cursor).
            patch["Cursor"] = {"CursorSize": int(round(12 * float(cursor)))}

    return json.dumps(patch, separators=(",", ":"))


async def download_replay_file(
    osu_api_client,
    score_id: int,
    output_dir: str,
    oauth_token: Optional[str] = None,
) -> Optional[str]:
    """Download .osr replay file. Returns path to the file or None. Pass a user's
    oauth_token (replays are only served to user tokens, not the guest app one)."""
    # Try osu! API v2 direct download
    replay_data = None
    try:
        replay_data = await osu_api_client.download_replay(score_id, oauth_token=oauth_token)
    except Exception as e:
        logger.debug(f"API replay download failed for {score_id}: {e}")

    # Fallback: public URL
    if not replay_data:
        try:
            url = f"https://osu.ppy.sh/scores/{score_id}/download"
            # requests via to_thread, not aiohttp/httpx — see download_beatmap's
            # note above (both async clients failed tunneling HTTPS through
            # the render worker's required proxy; harmless here if no proxy
            # is configured at all).
            def _sync_get():
                resp = requests.get(url, timeout=30.0, allow_redirects=True)
                return resp.status_code, resp.content

            status, body = await asyncio.to_thread(_sync_get)
            if status == 200:
                replay_data = body
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

    async def _run_once():
        """One danser subprocess attempt. Returns (returncode, output_lines)."""
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

        return proc.returncode, output_lines

    async with _render_slot(on_queue):
        logger.info(f"Starting danser render: {replay_path} -> {out_name}")
        logger.info(f"sPatch: {spatch}")

        # danser occasionally hits a GL-context startup race right after the
        # worker process (re)starts — a Go deadlock in its main GL thread
        # before a single frame renders, gone on a bare retry (observed
        # 2026-07-02: 2 occurrences, both recovered by simply running danser
        # again). Retry ONCE, but only when the output actually matches that
        # signature — a genuine render failure (bad beatmap/replay) shouldn't
        # be silently doubled in wall time.
        returncode, output_lines = await _run_once()
        if returncode != 0:
            tail = "\n".join(output_lines[-10:])
            if any(marker in tail for marker in ("locked to thread", "chan receive")):
                logger.warning(f"danser hit the known GL-context startup race (exit {returncode}), retrying once:\n{tail}")
                await asyncio.sleep(1.5)
                returncode, output_lines = await _run_once()

        if returncode != 0:
            tail = "\n".join(output_lines[-10:])
            logger.error(f"danser exited with code {returncode}:\n{tail}")
            raise DanserError(f"danser завершился с ошибкой (код {returncode})")

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
