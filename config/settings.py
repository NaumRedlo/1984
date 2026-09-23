import os
import logging
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE_DEFAULT")

TELEGRAM_BOT_API_URL = os.getenv("TELEGRAM_BOT_API_URL", "")
OSU_CLIENT_ID = os.getenv("OSU_CLIENT_ID")
OSU_CLIENT_SECRET = os.getenv("OSU_CLIENT_SECRET")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

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

_raw_render_ids = os.getenv("RENDER_TESTER_IDS", "")
RENDER_OPEN_TO_ALL: bool = _raw_render_ids.strip() == "*"
RENDER_TESTER_IDS: list[int] = [int(x.strip()) for x in _raw_render_ids.split(",") if x.strip().isdigit()]

RENDER_WORKER_TOKEN = os.getenv("RENDER_WORKER_TOKEN", "")

_raw_proxy_hops = os.getenv("TRUSTED_PROXY_HOPS", "1").strip()
TRUSTED_PROXY_HOPS: int = max(1, int(_raw_proxy_hops)) if _raw_proxy_hops.isdigit() else 1

OSU_OAUTH_REDIRECT_URI = os.getenv("OSU_OAUTH_REDIRECT_URI", "https://onenineeightfour.ignorelist.com/oauth/callback")
OSU_OAUTH_SCOPES = "public identify friends.read"
OAUTH_SERVER_PORT = int(os.getenv("OAUTH_SERVER_PORT", "8080"))
OAUTH_ENCRYPTION_KEY = os.getenv("OAUTH_ENCRYPTION_KEY", "")

DOSSIER_BIN = os.getenv("DOSSIER_BIN") or os.path.expanduser("~/.dossier/engine/dossier")

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
