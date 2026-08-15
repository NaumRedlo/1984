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

# Who may use anything render-related (Dossier, the in-house replay engine).
# Deliberately NOT ADMIN_IDS: the engine is under construction, its answers are
# provisional, and it runs a native binary and pulls beatmaps on demand. Empty
# means nobody — an unfinished renderer should ignore the world by default
# rather than answer it. Widen this only when the engine is worth showing.
_raw_render_ids = os.getenv("RENDER_TESTER_IDS", "")
RENDER_TESTER_IDS: list[int] = [int(x.strip()) for x in _raw_render_ids.split(",") if x.strip().isdigit()]

# Which Dossier skin the bot renders in. `classic` draws the map's own combo
# colours over the engine's neutral look — the only one there is, now that the
# project's house skin has been removed in favour of importing the skins
# players actually use. See dossier/crates/dossier-render/src/skin.rs.
DOSSIER_SKIN = os.getenv("DOSSIER_SKIN", "classic")

# How hard the encoder works. Once drawing is parallel the encoder becomes the
# wall, and these are the only knobs that move it: a faster preset trades file
# size for speed, a higher CRF trades quality for both. Measured on our content
# at 720p: veryfast/20 costs 7.6ms a frame for 900 KiB per twelve seconds,
# superfast/23 costs 2.8ms for 1.5 MiB, ultrafast/23 costs 1.5ms for 3.0 MiB.
DOSSIER_PRESET = os.getenv("DOSSIER_PRESET", "veryfast")
DOSSIER_CRF = os.getenv("DOSSIER_CRF", "20")

# How many threads the encoder may take. Empty leaves it to ffmpeg, which sizes
# its pool at about 1.5 per core knowing nothing about the drawing threads it
# shares the machine with — so both sides oversubscribe and slow each other
# down. Measured at 1080p on a two-core box: uncapped gave 51.5ms of drawing
# per frame against 26.1ms of piping, capping to one gave 19.8ms against 34.9,
# and two balanced them at 29.3 against 29.1 and was fastest. The rule the
# render report is for: move this until drawing-per-thread and piping meet.
DOSSIER_ENCODER_THREADS = os.getenv("DOSSIER_ENCODER_THREADS", "")

# The compiled `dossier` binary (see dossier/crates/dossier-cli). Built with
# `cargo build --release` inside dossier/; override when it lives elsewhere.
DOSSIER_BIN = os.getenv(
    "DOSSIER_BIN",
    os.path.join(PROJECT_ROOT, "dossier", "target", "release", "dossier"),
)

# Renders done on another machine (see services/render_farm). The shared secret
# a worker authenticates with: empty means the feature is off and its endpoints
# are never registered, which is the only safe reading of "no password set".
RENDER_WORKER_TOKEN = os.getenv("RENDER_WORKER_TOKEN", "")

# How long the bot offers a render to a worker before giving up and doing it
# itself. Short: this is the wait before *any* progress appears, and a laptop
# that is awake claims within a poll. A laptop that is off should cost the user
# a few seconds, not a choice.
RENDER_WORKER_WAIT = float(os.getenv("RENDER_WORKER_WAIT", "12"))

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
