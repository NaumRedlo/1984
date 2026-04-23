"""
BSK map pool populator — fetches beatmaps from osu! API and adds them to bsk_map_pool.
Uses beatmap search by star rating range.
"""

import asyncio
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.bsk_map_pool import BskMapPool
from utils.logger import get_logger

logger = get_logger("bsk.map_pool")


def _estimate_weights(bpm: float, ar: float, od: float, length: int) -> dict:
    """
    Rough heuristic weights before ML is available.
    High BPM → speed, high AR → aim, high OD → acc, long map → cons.
    """
    bpm_norm = min(bpm / 300.0, 1.0) if bpm else 0.5
    ar_norm = min(ar / 10.0, 1.0) if ar else 0.5
    od_norm = min(od / 10.0, 1.0) if od else 0.5
    len_norm = min(length / 300.0, 1.0) if length else 0.5

    raw = {
        'aim':   ar_norm,
        'speed': bpm_norm,
        'acc':   od_norm,
        'cons':  len_norm,
    }
    total = sum(raw.values()) or 1.0
    return {k: round(v / total, 3) for k, v in raw.items()}


def _map_type(weights: dict) -> str:
    best = max(weights, key=weights.get)
    return best  # aim | speed | acc | cons


async def add_map_to_pool(api_client, beatmap_id: int) -> Optional[BskMapPool]:
    """Fetch beatmap from osu! API and add to pool. Returns None if already exists or not found."""
    async with get_db_session() as session:
        existing = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if existing:
            logger.info(f"Map {beatmap_id} already in pool")
            return None

    data = await api_client.get_beatmap(beatmap_id)
    if not data:
        logger.warning(f"Beatmap {beatmap_id} not found in osu! API")
        return None

    bset = data.get("beatmapset") or {}
    bpm = float(data.get("bpm") or bset.get("bpm") or 0)
    ar = float(data.get("ar") or 0)
    od = float(data.get("accuracy") or 0)
    cs = float(data.get("cs") or 0)
    length = int(data.get("total_length") or data.get("hit_length") or 0)
    sr = float(data.get("difficulty_rating") or 0)

    weights = _estimate_weights(bpm, ar, od, length)

    map_entry = BskMapPool(
        beatmap_id=beatmap_id,
        beatmapset_id=int(data.get("beatmapset_id") or bset.get("id") or 0),
        title=bset.get("title") or data.get("version") or "Unknown",
        artist=bset.get("artist") or "Unknown",
        version=data.get("version") or "",
        creator=bset.get("creator"),
        star_rating=sr,
        bpm=bpm,
        length=length,
        ar=ar,
        od=od,
        cs=cs,
        w_aim=weights['aim'],
        w_speed=weights['speed'],
        w_acc=weights['acc'],
        w_cons=weights['cons'],
        map_type=_map_type(weights),
        enabled=True,
    )

    async with get_db_session() as session:
        session.add(map_entry)
        await session.commit()
        await session.refresh(map_entry)

    logger.info(f"Added map {beatmap_id} '{map_entry.title}' ({sr}★) to BSK pool")
    return map_entry


async def search_and_populate(
    api_client,
    sr_min: float = 3.0,
    sr_max: float = 7.0,
    target_count: int = 50,
) -> int:
    """
    Experimental: search ranked maps by star rating and populate pool.
    Returns number of maps added.
    """
    added = 0
    cursor = None

    while added < target_count:
        params = {
            "mode": "osu",
            "status": "ranked",
            "sort": "difficulty_rating_asc",
        }
        if cursor:
            params["cursor_string"] = cursor

        data = await api_client._make_request("GET", "beatmapsets/search", params=params)
        if not data:
            break

        beatmapsets = data.get("beatmapsets", [])
        if not beatmapsets:
            break

        for bset in beatmapsets:
            for bmap in bset.get("beatmaps", []):
                sr = float(bmap.get("difficulty_rating") or 0)
                if sr < sr_min or sr > sr_max:
                    continue
                result = await add_map_to_pool(api_client, int(bmap["id"]))
                if result:
                    added += 1
                if added >= target_count:
                    break
            if added >= target_count:
                break

        cursor = data.get("cursor_string")
        if not cursor:
            break

        await asyncio.sleep(0.5)

    logger.info(f"BSK pool populated: {added} maps added")
    return added
