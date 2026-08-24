import asyncio
from typing import Any, Iterable, Optional, Sequence

from utils.logger import get_logger

logger = get_logger("utils.osu.star_rating")


async def resolve(client, beatmap_id, mods, nominal) -> Optional[float]:
    if client is None or not beatmap_id:
        return nominal
    try:
        return await client.effective_sr(beatmap_id, mods, nominal)
    except Exception:  # noqa: BLE001 — a flaky endpoint must not lose the card
        logger.debug("star rating lookup failed for %s (%s)", beatmap_id, mods, exc_info=True)
        return nominal


async def fill(
    client,
    rows: Sequence[dict],
    *,
    beatmap_key: str = "beatmap_id",
    mods_key: str = "mods",
    sr_key: str = "eff_sr",
    nominal_key: str = "star_rating",
) -> None:
    if client is None or not rows:
        return
    wanted = [row for row in rows if isinstance(row, dict) and row.get(beatmap_key)]
    if not wanted:
        return
    found = await asyncio.gather(*(
        resolve(
            client,
            row.get(beatmap_key),
            row.get(mods_key),
            row.get(nominal_key) or row.get(sr_key),
        )
        for row in wanted
    ))
    for row, rating in zip(wanted, found):
        if rating is not None:
            row[sr_key] = rating


__all__ = ["fill", "resolve"]
