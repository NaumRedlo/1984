import os
import logging
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE_DEFAULT")
# When set (e.g. http://localhost:8081), route aiogram through a self-hosted
# telegram-bot-api server running with --local. Raises the upload limit from
# 50 MB to ~2 GB — required for shipping rendered replay videos. Empty = use
# the public cloud Bot API.
TELEGRAM_BOT_API_URL = os.getenv("TELEGRAM_BOT_API_URL", "")
OSU_CLIENT_ID = os.getenv("OSU_CLIENT_ID")
OSU_CLIENT_SECRET = os.getenv("OSU_CLIENT_SECRET")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# The DB holds ALL bot data (users, duels, ratings, tokens, bounties), so it's
# named botdata.db. Legacy fallback: it was historically bounties.db (misleading
# name). If a deployment hasn't renamed the file or set DATABASE_URL yet, keep
# reading the old file instead of silently creating an empty botdata.db and
# "losing" the data.
_default_db = os.path.join(PROJECT_ROOT, "botdata.db")
_legacy_db = os.path.join(PROJECT_ROOT, "bounties.db")
if not os.path.exists(_default_db) and os.path.exists(_legacy_db):
    _default_db = _legacy_db
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{_default_db}")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _raw_admin_ids.split(",") if x.strip().isdigit()]

_raw_contributor_ids = os.getenv("CONTRIBUTOR_IDS", "")
CONTRIBUTOR_IDS: list[int] = [int(x.strip()) for x in _raw_contributor_ids.split(",") if x.strip().isdigit()]

OSU_OAUTH_REDIRECT_URI = os.getenv("OSU_OAUTH_REDIRECT_URI", "https://onenineeightfour.mooo.com/oauth/callback")
OSU_OAUTH_SCOPES = "public identify"
OAUTH_SERVER_PORT = int(os.getenv("OAUTH_SERVER_PORT", "8080"))
OAUTH_ENCRYPTION_KEY = os.getenv("OAUTH_ENCRYPTION_KEY", "")

# Local danser-go replay renderer (CPU-only server: software GL via Xvfb +
# Mesa llvmpipe, see utils/osu/danser_renderer.py). Songs dir must match danser's
# own OsuSongsDir (default ~/.osu/Songs) — danser applies -sPatch after its DB
# init, so the beatmap we drop here is only found if this path equals danser's.
DANSER_PATH = os.getenv("DANSER_PATH", os.path.expanduser("~/danser/danser-cli"))
DANSER_SONGS_DIR = os.getenv("DANSER_SONGS_DIR", os.path.expanduser("~/.osu/Songs"))
# Max render video size to send. Cloud Bot API caps at 50 MB; a local Bot API
# server (TELEGRAM_BOT_API_URL) allows up to ~2 GB — default tracks that.
RENDER_MAX_VIDEO_MB = int(os.getenv("RENDER_MAX_VIDEO_MB", "1900" if TELEGRAM_BOT_API_URL else "50"))
# Max seconds the bot waits between bytes from the render worker. The worker is
# silent for the whole render+fit (no progress streaming), so this must exceed the
# longest render — minutes for a marathon at 1080p. Default 30 min.
RENDER_WORKER_READ_TIMEOUT = int(os.getenv("RENDER_WORKER_READ_TIMEOUT", "1800"))
# Concurrent danser renders. Software GL saturates every core, so keep this at 1
# on the CPU-only server; raise only with hardware acceleration.
RENDER_CONCURRENCY = int(os.getenv("RENDER_CONCURRENCY", "1"))

# GPU rendering (NVIDIA). When RENDER_GPU=1 the renderer drives a real GPU-backed
# Xorg (RENDER_DISPLAY) instead of Xvfb+llvmpipe, and encodes with NVENC instead
# of CPU libx264 — see utils/osu/danser_renderer. Requires a headless Xorg on the
# card and an ffmpeg with h264_nvenc. Default off so the CPU-only bot server is
# unaffected. RENDER_DISPLAY is the X display the headless server runs on.
RENDER_GPU = os.getenv("RENDER_GPU", "0") == "1"
RENDER_DISPLAY = os.getenv("RENDER_DISPLAY", ":0")
# Use HEVC/H.265 (hevc_nvenc) instead of H.264 in GPU mode — ~40% better quality
# per byte, so 1080p60 keeps more detail under the 50 MB cap. Telegram plays HEVC
# mp4. Only meaningful with RENDER_GPU. Default off (H.264 is the safest default).
RENDER_HEVC = os.getenv("RENDER_HEVC", "0") == "1"
# Resolution/FPS used in GPU mode (the A10 handles 1080p60 easily). CPU mode
# stays at the per-user 720/540 from UserRenderSettings.
RENDER_GPU_RESOLUTION = os.getenv("RENDER_GPU_RESOLUTION", "1920x1080")
# After rendering, if the file exceeds this many MB it is re-encoded (NVENC in GPU
# mode) to a bitrate computed from its duration so it fits — this is how 1080p60
# is kept under Telegram's 50 MB cloud cap. 0 disables the fit step. Set on the
# render worker (e.g. 50); the bot's own send cap (RENDER_MAX_VIDEO_MB) still
# applies as the final guard.
RENDER_FIT_MAX_MB = int(os.getenv("RENDER_FIT_MAX_MB", "0"))

# On-demand GPU power management (Intelion Cloud). When RENDER_AUTOPOWER=1 the bot
# powers the GPU render server on before a render and off once no renders remain
# in flight (Intelion bills per-second; a stopped server is free). Readiness is
# detected via the worker's /health, not the Intelion status field, so it also
# confirms the OS + Xorg + worker are up. Default off — leave the worker always-on
# behaviour unchanged when unset. Token/id live in .env (never committed).
RENDER_AUTOPOWER = os.getenv("RENDER_AUTOPOWER", "0") == "1"
INTELION_API_URL = os.getenv("INTELION_API_URL", "https://intelion.cloud/api/v2")
INTELION_API_TOKEN = os.getenv("INTELION_API_TOKEN", "")
INTELION_SERVER_ID = os.getenv("INTELION_SERVER_ID", "")
# Max seconds to wait for the worker /health after powering the server on (cold
# boot + Xorg + worker start).
RENDER_WAKE_TIMEOUT = int(os.getenv("RENDER_WAKE_TIMEOUT", "240"))
# Keep the GPU server warm this many seconds after the last render finishes, so a
# burst of requests doesn't pay a cold start each time. 0 = power off immediately.
RENDER_WARM_SECONDS = int(os.getenv("RENDER_WARM_SECONDS", "300"))

# Remote render worker (optional CPU offload to a second server). When
# RENDER_WORKER_URL is empty the bot renders locally (default, unchanged). When
# set, the bot POSTs the .osr + beatmapset_id + settings to the worker over HTTP
# and streams back the mp4 — see services/render_worker and utils/osu/render_client.
# Security v1: shared Bearer secret + firewall the worker port to the bot's IP.
# NOTE: offloading the render does NOT lift Telegram's 50 MB send cap (that is
# still governed by RENDER_MAX_VIDEO_MB / TELEGRAM_BOT_API_URL on the bot side).
RENDER_WORKER_URL = os.getenv("RENDER_WORKER_URL", "")
RENDER_WORKER_SECRET = os.getenv("RENDER_WORKER_SECRET", "")
# Worker bind address (used by `python -m services.render_worker`). Binds to all
# interfaces; the firewall — not a localhost bind — is the access boundary, since
# the bot reaches the worker across the public internet.
RENDER_WORKER_PORT = int(os.getenv("RENDER_WORKER_PORT", "8090"))
RENDER_WORKER_BIND = os.getenv("RENDER_WORKER_BIND", "0.0.0.0")

_raw_group_id = os.getenv("GROUP_CHAT_ID", "")
GROUP_CHAT_ID: int | None = int(_raw_group_id) if _raw_group_id.lstrip("-").isdigit() else None

# Optional: a forum topic (message_thread_id) where DUEL duel cards (challenge,
# round, pool, finish) are routed. When set, `duel <nick>` and the challenge
# button post the duel into this topic regardless of where the command was
# invoked. When unset, duels go to the chat/topic where the command was issued.
_raw_duel_thread = os.getenv("DUEL_THREAD_ID", "")
DUEL_THREAD_ID: int | None = int(_raw_duel_thread) if _raw_duel_thread.isdigit() else None

# Bancho IRC
OSU_IRC_USERNAME = os.getenv("OSU_IRC_USERNAME", "")
OSU_IRC_PASSWORD = os.getenv("OSU_IRC_PASSWORD", "")


def validate_settings() -> None:
    missing = []
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE_DEFAULT":
        missing.append("TELEGRAM_BOT_TOKEN")
    if not OSU_CLIENT_ID:
        missing.append("OSU_CLIENT_ID")
    if not OSU_CLIENT_SECRET:
        missing.append("OSU_CLIENT_SECRET")
    if not OAUTH_ENCRYPTION_KEY:
        missing.append("OAUTH_ENCRYPTION_KEY")
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Please set them in .env file or environment."
        )
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS is empty. No users will have admin access.")
