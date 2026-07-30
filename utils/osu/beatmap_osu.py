"""One `.osu` file, straight from osu!.

The mirror path next door fetches a whole `.osz` from a third party, and that is
the right thing to want — an archive carries the audio, and a render without
audio is half a render. It is the wrong thing to *depend* on. A mirror has what
somebody uploaded to it; a graveyard map, a map pulled from the mirror, or a
mirror simply down all end the same way, with a replay nobody can judge.

`https://osu.ppy.sh/osu/<beatmap_id>` is the official raw `.osu`. No key, no
mirror, and it answers for every map that exists. What it does not carry is the
song — so this is not a replacement for the archive, it is the floor under it:
judging always works, and audio works when the mirror is there.
"""

import asyncio
import hashlib
import os

import requests

from config.settings import BEATMAP_STORE_DIR
from utils.logger import get_logger

logger = get_logger("utils.beatmap.osu")

_OFFICIAL = "https://osu.ppy.sh/osu/{beatmap_id}"

# osu!'s own ceiling, from its `BeatmapStore`. A `.osu` past this is not a
# beatmap.
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
    """Store it, or say why it is not the file we asked for."""
    if not content:
        # ppy answers 200 with nothing at all for a map deleted since it was
        # played. The id is real, the file is not.
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
        # osu! serves the map as it is *now*. A map revised since the replay was
        # set comes back a different file — which would be judged against the
        # wrong notes without ever looking wrong.
        logger.warning(
            "beatmap %s has been revised since: got %s, wanted %s", beatmap_id, got, checksum
        )
        return False

    target = path_for(checksum)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    # Written beside the target and moved, so an interrupted fetch never leaves
    # half a beatmap where a whole one is expected.
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
    """Put this exact `.osu` in the store. True when it is there afterwards."""
    if not beatmap_id or not checksum:
        return False
    if already_present(checksum):
        return True
    content = await asyncio.to_thread(_fetch, int(beatmap_id))
    if content is None:
        return False
    return await asyncio.to_thread(_keep, content, checksum, int(beatmap_id))
