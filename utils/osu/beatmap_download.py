import asyncio
import os

import requests

from utils.logger import get_logger
from services.dossier.settings import BEATMAP_STORE_DIR

logger = get_logger("utils.beatmap")

_BEATMAP_MIRRORS = [
    "https://catboy.best/d/{beatmapset_id}",
    "https://api.nerinyan.moe/d/{beatmapset_id}",
    "https://beatconnect.io/b/{beatmapset_id}",
    "https://osu.direct/d/{beatmapset_id}",
]

_DOWNLOAD_RETRIES = 3
_DOWNLOAD_RETRY_SECONDS = 2.0

_MAX_OSZ_BYTES = 200 * 1024 * 1024

_DOWNLOAD_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _beatmap_already_present(beatmapset_id: int) -> bool:
    songs_dir = os.path.expanduser(BEATMAP_STORE_DIR)
    os.makedirs(songs_dir, exist_ok=True)
    return any(e.startswith(str(beatmapset_id)) for e in os.listdir(songs_dir))


async def fetch_beatmap_osz(beatmapset_id: int):
    headers = {"User-Agent": _DOWNLOAD_UA}

    def _sync_get(url: str):
        with requests.get(
            url, headers=headers, timeout=120.0, allow_redirects=True, stream=True
        ) as resp:
            if resp.status_code != 200:
                return resp.status_code, b""
            data = bytearray()
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                data.extend(chunk)
                if len(data) > _MAX_OSZ_BYTES:
                    raise ValueError(f"over {_MAX_OSZ_BYTES // (1024 * 1024)}MB")
            return resp.status_code, bytes(data)

    for attempt in range(1, _DOWNLOAD_RETRIES + 1):
        for mirror_tpl in _BEATMAP_MIRRORS:
            url = mirror_tpl.format(beatmapset_id=beatmapset_id)
            try:
                status, data = await asyncio.to_thread(_sync_get, url)
                if status != 200:
                    logger.info(f"Mirror {url} returned {status} (attempt {attempt}/{_DOWNLOAD_RETRIES})")
                    continue
                if len(data) < 1000 or data[:2] != b"PK":
                    logger.info(f"Mirror {url} returned non-osz ({len(data)}b, attempt {attempt}/{_DOWNLOAD_RETRIES})")
                    continue
                logger.info(f"Fetched beatmap {beatmapset_id} ({len(data)} bytes)")
                return data
            except Exception as e:
                logger.info(f"Mirror {url} failed (attempt {attempt}/{_DOWNLOAD_RETRIES}): {e}")
                continue
        if attempt < _DOWNLOAD_RETRIES:
            await asyncio.sleep(_DOWNLOAD_RETRY_SECONDS)

    logger.warning(f"Failed to download beatmap {beatmapset_id} from all mirrors after {_DOWNLOAD_RETRIES} attempts")
    return None


async def download_beatmap(beatmapset_id: int) -> bool:
    if _beatmap_already_present(beatmapset_id):
        return True
    data = await fetch_beatmap_osz(beatmapset_id)
    if data is None:
        return False
    songs_dir = os.path.expanduser(BEATMAP_STORE_DIR)
    osz_path = os.path.join(songs_dir, f"{beatmapset_id}.osz")
    with open(osz_path, "wb") as f:
        f.write(data)
    return True


def save_beatmap_osz(beatmapset_id: int, osz_bytes: bytes) -> bool:
    if _beatmap_already_present(beatmapset_id):
        return True
    if len(osz_bytes) < 1000 or osz_bytes[:2] != b"PK":
        return False
    songs_dir = os.path.expanduser(BEATMAP_STORE_DIR)
    osz_path = os.path.join(songs_dir, f"{beatmapset_id}.osz")
    with open(osz_path, "wb") as f:
        f.write(osz_bytes)
    logger.info(f"Saved beatmap {beatmapset_id} from bot-provided bytes ({len(osz_bytes)} bytes)")
    return True
