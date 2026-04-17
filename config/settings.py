import os
import logging
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE_DEFAULT")
OSU_CLIENT_ID = os.getenv("OSU_CLIENT_ID")
OSU_CLIENT_SECRET = os.getenv("OSU_CLIENT_SECRET")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{os.path.join(PROJECT_ROOT, 'bounties.db')}")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _raw_admin_ids.split(",") if x.strip().isdigit()]

_raw_contributor_ids = os.getenv("CONTRIBUTOR_IDS", "")
CONTRIBUTOR_IDS: list[int] = [int(x.strip()) for x in _raw_contributor_ids.split(",") if x.strip().isdigit()]

ORDR_API_KEY = os.getenv("ORDR_API_KEY", "")

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
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Please set them in .env file or environment."
        )
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS is empty. No users will have admin access.")
