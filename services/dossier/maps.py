"""Getting the beatmap a replay was played on.

An `.osr` names its map by MD5 and nothing else — no id, no title. So the hash
goes to the osu! API to become a set id, and the set id goes to the mirror to
become an `.osz` in the local store. From there the engine finds the right
difficulty itself by hashing what's inside the archive.
"""

from typing import Optional

from config.settings import BEATMAP_STORE_DIR
from utils.logger import get_logger
from utils.osu.beatmap_download import download_beatmap

logger = get_logger("services.dossier.maps")


class MapUnavailable(RuntimeError):
    """Couldn't put the map on disk. The message is shown to a render tester."""


def songs_dir() -> str:
    return BEATMAP_STORE_DIR


async def ensure_map(osu_api_client, checksum: str) -> dict:
    """Make sure the map with this `.osu` MD5 is in the local store.

    Returns the API's beatmap record, so the caller can name the map even when
    the engine only ever saw a hash.
    """
    if not checksum:
        raise MapUnavailable("реплей не назвал карту (пустой хэш)")

    try:
        beatmap = await osu_api_client.lookup_beatmap_by_checksum(checksum)
    except Exception as exc:  # noqa: BLE001 — network/API shape is out of our hands
        logger.warning("checksum lookup failed for %s: %s", checksum, exc)
        raise MapUnavailable(f"osu! API не ответил на запрос карты: {exc}") from exc

    if not beatmap:
        # Unsubmitted, deleted, or a local edit — genuinely unfetchable, not a
        # transient failure, so say so plainly instead of retrying.
        raise MapUnavailable(
            f"карта {checksum} не найдена в osu! — вероятно, она не залита или изменена локально"
        )

    beatmapset_id = beatmap.get("beatmapset_id")
    if not beatmapset_id:
        raise MapUnavailable(f"osu! вернул карту без beatmapset_id: {beatmap.get('id')}")

    if not await download_beatmap(int(beatmapset_id)):
        raise MapUnavailable(f"не удалось скачать сет {beatmapset_id} с зеркала")

    return beatmap


def describe(beatmap: Optional[dict]) -> str:
    """Human name for a map from the API record, falling back gracefully — the
    nested beatmapset is present on lookups but not on every endpoint."""
    if not beatmap:
        return "неизвестная карта"
    beatmapset = beatmap.get("beatmapset") or {}
    artist = beatmapset.get("artist") or ""
    title = beatmapset.get("title") or ""
    version = beatmap.get("version") or ""
    if artist and title:
        return f"{artist} — {title} [{version}]".strip()
    return title or version or f"карта {beatmap.get('id', '?')}"
