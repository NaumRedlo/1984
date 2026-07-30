"""Getting the beatmap a replay was played on.

An `.osr` names its map by MD5 and nothing else — no id, no title. So the hash
goes to the osu! API to become a beatmap, and from there two things are fetched
for two different reasons.

**The `.osu` comes from osu! itself** — `osu.ppy.sh/osu/<id>`, no key, answers for
every map that exists. This is what judging needs, and it is not allowed to
depend on a third party.

**The `.osz` comes from a mirror**, best-effort, and carries the song. A render
without audio is half a render, so it is worth trying; it is not worth failing
over. A graveyard map, a map missing from the mirror, or a mirror simply down
used to end with a replay nobody could judge. Now it ends with a silent video,
and the reason is logged.
"""

from typing import Optional

from config.settings import BEATMAP_STORE_DIR
from utils.logger import get_logger
from utils.osu.beatmap_download import download_beatmap
from utils.osu.beatmap_osu import download_osu

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

    # The archive first, because it carries the audio and the engine prefers a
    # loose `.osu` over an archive when both are present — so a fallback file
    # written now would win and take the sound with it.
    beatmapset_id = beatmap.get("beatmapset_id")
    if beatmapset_id and await download_beatmap(int(beatmapset_id)):
        return beatmap

    # The mirror had nothing, or nothing to say. osu! itself always does.
    if await download_osu(beatmap.get("id"), checksum):
        logger.warning(
            "beatmap %s came from osu! rather than a mirror — judging works, "
            "the render will be silent",
            beatmap.get("id"),
        )
        # Marked so the render can say so out loud. With four mirrors this should
        # be rare, and a silent video that arrives without warning reads as a
        # broken render rather than as a missing archive.
        beatmap["_no_audio"] = True
        return beatmap

    raise MapUnavailable(
        f"карту {checksum} не удалось взять ни с зеркала, ни у osu! — "
        "возможно, она удалена или изменена после реплея"
    )


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
