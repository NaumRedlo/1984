import asyncio
import hashlib
import os

import requests

from services.dossier.settings import BEATMAP_STORE_DIR
from utils.logger import get_logger

logger = get_logger("utils.beatmap.osu")

_OFFICIAL = "https://osu.ppy.sh/osu/{beatmap_id}"

_MAX_BYTES = 50 * 1024 * 1024
_TIMEOUT_SECONDS = 20


def path_for(checksum: str) -> str:
    songs = os.path.expanduser(BEATMAP_STORE_DIR)
    return os.path.join(songs, f"{checksum}.osu")


def already_present(checksum: str) -> bool:
    return os.path.isfile(path_for(checksum))


def _fetch(beatmap_id: int) -> bytes | None:
    try:
        response = requests.get(
            _OFFICIAL.format(beatmap_id=beatmap_id),
            timeout=_TIMEOUT_SECONDS,
            headers={"User-Agent": "dossier-bot"},
        )
    except requests.RequestException as exc:
        logger.warning("osu! did not answer for beatmap %s: %s", beatmap_id, exc)
        return None
    if response.status_code != 200:
        logger.warning("osu! answered %s for beatmap %s", response.status_code, beatmap_id)
        return None
    return response.content


def _keep(content: bytes, checksum: str, beatmap_id: int) -> bool:
    if not content:
        logger.warning("beatmap %s has been deleted from osu!", beatmap_id)
        return False
    if len(content) > _MAX_BYTES:
        logger.warning("beatmap %s is over the size ceiling", beatmap_id)
        return False
    header = content[:64].decode("utf-8-sig", "replace").lstrip()
    if not header.startswith("osu file format v"):
        logger.warning("beatmap %s did not come back as a .osu file", beatmap_id)
        return False
    got = hashlib.md5(content).hexdigest()
    if got != checksum:
        logger.warning(
            "beatmap %s has been revised since: got %s, wanted %s", beatmap_id, got, checksum
        )
        return False

    target = path_for(checksum)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary = f"{target}.part"
    try:
        with open(temporary, "wb") as handle:
            handle.write(content)
        os.replace(temporary, target)
    except OSError as exc:
        logger.warning("could not store beatmap %s: %s", beatmap_id, exc)
        return False
    return True


async def download_osu(beatmap_id: int, checksum: str) -> bool:
    if not beatmap_id or not checksum:
        return False
    if already_present(checksum):
        return True
    content = await asyncio.to_thread(_fetch, int(beatmap_id))
    if content is None:
        return False
    return await asyncio.to_thread(_keep, content, checksum, int(beatmap_id))
