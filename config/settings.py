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
# Mesa llvmpipe, see utils/osu/danser_renderer.py).
DANSER_PATH = os.getenv("DANSER_PATH", os.path.expanduser("~/danser/danser-cli"))
DANSER_SONGS_DIR = os.getenv("DANSER_SONGS_DIR", os.path.expanduser("~/danser/Songs"))
# Max render video size to send. Cloud Bot API caps at 50 MB; a local Bot API
# server (TELEGRAM_BOT_API_URL) allows up to ~2 GB — default tracks that.
RENDER_MAX_VIDEO_MB = int(os.getenv("RENDER_MAX_VIDEO_MB", "1900" if TELEGRAM_BOT_API_URL else "50"))
# Concurrent danser renders. Software GL saturates every core, so keep this at 1
# on the CPU-only server; raise only with hardware acceleration.
RENDER_CONCURRENCY = int(os.getenv("RENDER_CONCURRENCY", "1"))

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
