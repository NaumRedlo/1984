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

# Where downloaded beatmap .osz files are stored (utils/osu/beatmap_download.py).
# Reads the legacy DANSER_SONGS_DIR env var as a fallback so existing deployments
# keep their current store after the replay renderer was removed.
BEATMAP_STORE_DIR = os.getenv(
    "BEATMAP_STORE_DIR",
    os.getenv("DANSER_SONGS_DIR", os.path.expanduser("~/.osu/Songs")),
)

_raw_group_id = os.getenv("GROUP_CHAT_ID", "")
GROUP_CHAT_ID: int | None = int(_raw_group_id) if _raw_group_id.lstrip("-").isdigit() else None


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
