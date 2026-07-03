"""Local danser-go renderer for osu! replays.

Requires danser-cli, xvfb-run, ffmpeg installed on the server.
CPU-only rendering via Mesa software (LIBGL_ALWAYS_SOFTWARE=1).
"""

import asyncio
import io
import json
import os
import re
import tempfile
import shutil
import zipfile
from contextlib import asynccontextmanager
from typing import Optional, Dict, Callable, Awaitable

import aiohttp

from utils.logger import get_logger
from config.settings import (
    DANSER_PATH,
    DANSER_SONGS_DIR,
    DANSER_SKINS_DIR,
    RENDER_CONCURRENCY,
    RENDER_GPU,
    RENDER_DISPLAY,
    RENDER_GPU_RESOLUTION,
    RENDER_HEVC,
    RENDER_NVENC_PRESET,
    RENDER_FIT_MAX_MB,
)

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

logger = get_logger("utils.danser")

# Render at most RENDER_CONCURRENCY at a time (1 on the CPU-only box — software
# GL saturates every core). Extra requests wait FIFO; _inflight counts everyone
# waiting+rendering so callers can show a queue position. Beyond _MAX_QUEUE we
# reject rather than let the backlog grow unbounded.
_render_semaphore = asyncio.Semaphore(RENDER_CONCURRENCY)
_MAX_QUEUE = 10
_inflight = 0

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


class DanserError(Exception):
    """Raised when danser-cli fails."""
    pass


class DanserNotFoundError(DanserError):
    """Raised when danser-cli binary is not found."""
    pass


class RenderQueueFullError(DanserError):
    """Raised when too many renders are queued."""
    pass


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

    # Download from mirrors, retrying the whole pass a few times — with only
    # one mirror left (see _BEATMAP_MIRRORS' note) there's no second mirror to
    # fall back on, so a transient failure needs its own resilience here.
    # Per-attempt outcomes are logged at INFO (was DEBUG) so a future "failed
    # from all mirrors" is diagnosable straight from the normal-level logs,
    # not just the final aggregate WARNING.
    #
    # 2026-07-03: root cause of the "hangs for the full 120s timeout, empty
    # exception" failures — the render worker's outbound internet is proxied
    # (http(s)_proxy env vars set on the VM), and unlike curl/requests,
    # aiohttp does NOT read those env vars unless trust_env=True is passed.
    # Without it, every request tried to connect directly (bypassing the
    # required proxy) and hung until the timeout instead of ever connecting.
    timeout = aiohttp.ClientTimeout(total=120)
    headers = {"User-Agent": _DOWNLOAD_UA}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, trust_env=True) as session:
        for attempt in range(1, _DOWNLOAD_RETRIES + 1):
            for mirror_tpl in _BEATMAP_MIRRORS:
                url = mirror_tpl.format(beatmapset_id=beatmapset_id)
                try:
                    async with session.get(url, allow_redirects=True) as resp:
                        if resp.status != 200:
                            logger.info(f"Mirror {url} returned {resp.status} (attempt {attempt}/{_DOWNLOAD_RETRIES})")
                            continue
                        data = await resp.read()
                        # An .osz is a zip — must start with "PK". Some mirrors answer
                        # 200 with a small HTML landing/error page when a set is missing;
                        # reject that so we don't save a corrupt map and fall through to
                        # the next mirror.
                        if len(data) < 1000 or data[:2] != b"PK":
                            logger.info(f"Mirror {url} returned non-osz ({len(data)}b, attempt {attempt}/{_DOWNLOAD_RETRIES})")
                            continue
                        osz_path = os.path.join(songs_dir, f"{beatmapset_id}.osz")
                        with open(osz_path, "wb") as f:
                            f.write(data)
                        logger.info(f"Downloaded beatmap {beatmapset_id} ({len(data)} bytes)")
                        return True
                except Exception as e:
                    logger.info(f"Mirror {url} failed (attempt {attempt}/{_DOWNLOAD_RETRIES}): {e}")
                    continue
            if attempt < _DOWNLOAD_RETRIES:
                await asyncio.sleep(_DOWNLOAD_RETRY_SECONDS)

    logger.warning(f"Failed to download beatmap {beatmapset_id} from all mirrors after {_DOWNLOAD_RETRIES} attempts")
    return False


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
            timeout = aiohttp.ClientTimeout(total=30)
            # trust_env=True: see download_beatmap's note above — harmless
            # no-op unless an http(s)_proxy env var is actually set.
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
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


_FIT_AUDIO_KBPS = 128
# Aim well under the cap: NVENC VBR overshoots its target by ~15-20%, so 0.82
# usually lands under the cap on the first attempt (the iterative retry is a
# backstop, but each attempt is a full re-encode — expensive on long maps).
_FIT_SAFETY = 0.82
_FIT_MAX_ATTEMPTS = 3


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


# ── custom skins (.osk) ──

# Deny-list, not an allow-list: real osu! skin names use parentheses, brackets,
# punctuation, and non-Latin scripts ("Skin (v2)", "★Skin★", "スキン") — only the
# genuinely filesystem-dangerous characters are stripped (path separators, NUL,
# other control chars). Traversal via a bare "." / ".." (no slashes needed for
# os.path.join to walk up) is blocked explicitly below since dots are otherwise
# allowed through.
_SKIN_NAME_DENY_RE = re.compile(r"[\\/\x00-\x1f\x7f]+")


def sanitize_skin_name(name: str) -> str:
    """A safe folder name for a skin (no path separators / traversal / control
    characters) — otherwise permissive."""
    name = os.path.basename((name or "").strip())
    if name.lower().endswith(".osk"):
        name = name[:-4]
    name = _SKIN_NAME_DENY_RE.sub("", name).strip()
    if name in (".", ".."):
        return ""
    return name[:64]


def list_skins() -> list:
    """Skin folder names present in DANSER_SKINS_DIR."""
    skins_dir = os.path.expanduser(DANSER_SKINS_DIR)
    if not os.path.isdir(skins_dir):
        return []
    return sorted(
        e for e in os.listdir(skins_dir)
        if os.path.isdir(os.path.join(skins_dir, e))
    )


def install_skin(osk_bytes: bytes, name: str) -> str:
    """Unpack an .osk (a zip) into DANSER_SKINS_DIR/<name>/. Returns the installed
    skin name. Raises DanserError on a bad/unsafe archive."""
    safe = sanitize_skin_name(name)
    if not safe:
        raise DanserError("Некорректное имя скина.")
    skins_dir = os.path.expanduser(DANSER_SKINS_DIR)
    dest = os.path.join(skins_dir, safe)
    os.makedirs(dest, exist_ok=True)

    dest_abs = os.path.abspath(dest)
    try:
        with zipfile.ZipFile(io.BytesIO(osk_bytes)) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                target = os.path.normpath(os.path.join(dest_abs, member))
                # Reject absolute paths / traversal (zip-slip).
                if target != dest_abs and not target.startswith(dest_abs + os.sep):
                    raise DanserError("Небезопасный архив скина (path traversal).")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except zipfile.BadZipFile:
        raise DanserError("Файл не является корректным .osk (zip).")

    logger.info(f"Installed skin '{safe}' into {dest}")
    return safe


def delete_skin(name: str) -> None:
    """Remove a skin folder from DANSER_SKINS_DIR. Raises DanserError if the
    name is invalid or the skin doesn't exist."""
    safe = sanitize_skin_name(name)
    if not safe or safe != name:
        raise DanserError("Некорректное имя скина.")
    skins_dir = os.path.expanduser(DANSER_SKINS_DIR)
    target = os.path.join(skins_dir, safe)
    if not os.path.isdir(target):
        raise DanserError("Скин не найден.")
    shutil.rmtree(target)
    logger.info(f"Deleted skin '{safe}'")


def rename_skin(name: str, new_name: str) -> str:
    """Rename a skin folder. Returns the sanitized new name actually used.
    Raises DanserError if the source is missing/invalid or the target name is
    invalid or already taken."""
    safe = sanitize_skin_name(name)
    if not safe or safe != name:
        raise DanserError("Некорректное текущее имя скина.")
    safe_new = sanitize_skin_name(new_name)
    if not safe_new:
        raise DanserError("Некорректное новое имя скина.")
    skins_dir = os.path.expanduser(DANSER_SKINS_DIR)
    src = os.path.join(skins_dir, safe)
    if not os.path.isdir(src):
        raise DanserError("Скин не найден.")
    dest = os.path.join(skins_dir, safe_new)
    if os.path.exists(dest):
        raise DanserError("Скин с таким именем уже существует.")
    os.rename(src, dest)
    logger.info(f"Renamed skin '{safe}' -> '{safe_new}'")
    return safe_new
