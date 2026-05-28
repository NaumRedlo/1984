"""HPS map pool ingest / refresh.

Plan: unified-giggling-tiger (step 9/9).

HPS-side counterpart to `services/bsk/map_pool.py`. Fetches a beatmap from
the osu! API + .osu file, computes the HPS profile via
`services.hps.hps_profile.compute_hps_profile`, and writes an HpsMapPool
row. No ML calibration, no per-axis stars — just the rule-tagged
metadata + JSON typing hints used by the weekly bounty generator.

Public surface:
  - add_map_to_hps_pool(api_client, beatmap_id) -> HpsMapPool | None
  - refresh_hps_map(api_client, beatmap_id) -> dict (status report)
  - hps_map_is_broken(entry) -> (bool, list[str])

Intentionally mirrors the BSK module so the admin commands and any future
maintenance scripts look familiar. We don't reuse the BSK functions
because they pull in the ML calibration stack which HPS doesn't need.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from sqlalchemy import select

from db.database import get_db_session
from db.models.hps_map_pool import HpsMapPool
from services.hps.hps_profile import compute_hps_profile
from utils.logger import get_logger

logger = get_logger("hps.map_pool")


# ─── Apply profile to a row ──────────────────────────────────────────────────


def apply_profile_to_entry(entry: HpsMapPool, profile: dict) -> None:
    """Write a `compute_hps_profile(...)` result onto an HpsMapPool ORM row.

    Caller owns the session / commit. `typing_hints` is JSON-serialised
    so the column can be queried as text by SQL (`json_extract`) without
    a binary blob path.
    """
    entry.genre_tag     = profile.get("genre_tag")
    entry.length_bucket = profile.get("length_bucket")
    entry.bpm_bucket    = profile.get("bpm_bucket")
    entry.ranked_status = profile.get("ranked_status")
    hints = profile.get("typing_hints") or {}
    entry.typing_hints  = json.dumps(hints, ensure_ascii=False) if hints else None


# ─── Ingest ──────────────────────────────────────────────────────────────────


async def add_map_to_hps_pool(api_client, beatmap_id: int) -> Optional[HpsMapPool]:
    """Fetch + ingest a beatmap into hps_map_pool.

    Idempotent: returns None if the beatmap already exists. Caller can use
    `refresh_hps_map` to update an existing row.
    """
    async with get_db_session() as session:
        existing = (await session.execute(
            select(HpsMapPool).where(HpsMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if existing:
            logger.info(f"Map {beatmap_id} already in HPS pool")
            return None

    data = await api_client.get_beatmap(beatmap_id)
    if not data:
        logger.warning(f"Beatmap {beatmap_id} not found in osu! API")
        return None

    bset      = data.get("beatmapset") or {}
    bpm       = float(data.get("bpm")  or bset.get("bpm")  or 0)
    ar        = float(data.get("ar")        or 0)
    od        = float(data.get("accuracy")  or 0)
    cs        = float(data.get("cs")        or 0)
    length    = int(data.get("total_length") or data.get("hit_length") or 0)
    sr        = float(data.get("difficulty_rating") or 0)
    max_combo = int(data.get("max_combo") or 0)
    status    = (bset.get("status") or data.get("status") or "ranked").lower()

    osu_text = None
    osu_bytes = await api_client.download_osu_file(beatmap_id)
    if osu_bytes:
        try:
            osu_text = osu_bytes.decode("utf-8", errors="replace")
        except Exception:
            osu_text = None

    profile = compute_hps_profile(
        osu_text,
        bpm=bpm, ar=ar, od=od,
        length_s=length, star_rating=sr,
        ranked_status=status,
    )

    entry = HpsMapPool(
        beatmap_id=beatmap_id,
        beatmapset_id=int(data.get("beatmapset_id") or bset.get("id") or 0),
        title=bset.get("title") or data.get("version") or "Unknown",
        artist=bset.get("artist") or "Unknown",
        version=data.get("version") or "",
        creator=bset.get("creator"),
        star_rating=sr,
        bpm=bpm,
        length=length,
        ar=ar, od=od, cs=cs,
        max_combo=max_combo,
        enabled=True,
    )
    apply_profile_to_entry(entry, profile)

    async with get_db_session() as session:
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

    logger.info(
        f"Added map {beatmap_id} '{entry.title}' ({sr}★, "
        f"{entry.length_bucket}/{entry.bpm_bucket}/{entry.genre_tag}) to HPS pool"
    )
    return entry


# ─── Refresh / repair ────────────────────────────────────────────────────────


async def _retry(coro_factory, attempts: int = 3, delay: float = 0.6):
    """Call `coro_factory()` up to N times, returning the first truthy result.

    Mirrors services/bsk/map_pool.py:_retry so the two ingest pipelines
    behave identically against flaky CDN / API failures.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            r = await coro_factory()
            if r:
                return r
        except Exception as e:
            last_exc = e
            logger.debug(f"_retry attempt {i+1}/{attempts}: {e}")
        if i < attempts - 1:
            await asyncio.sleep(delay * (i + 1))
    if last_exc:
        logger.warning(f"_retry exhausted: {last_exc}")
    return None


def hps_map_is_broken(entry: HpsMapPool) -> tuple[bool, list[str]]:
    """Return (is_broken, reasons). 'broken' means refresh is worth trying."""
    reasons: list[str] = []
    if not entry.star_rating or entry.star_rating <= 0:
        reasons.append("sr=0")
    if not entry.title or entry.title == "Unknown":
        reasons.append("no_metadata")
    if entry.typing_hints is None:
        reasons.append("no_typing_hints")
    return (bool(reasons), reasons)


async def refresh_hps_map(api_client, beatmap_id: int, *, re_enable: bool = True) -> dict:
    """Re-pull metadata + .osu file for an existing HPS pool entry.

    Returns a status dict identical in shape to
    `services.bsk.map_pool.refresh_map` so the admin handler can use one
    UI template for both pools.
    """
    out: dict = {
        "beatmap_id": beatmap_id, "status": "error",
        "reasons": [], "updated": [], "message": "",
    }

    async with get_db_session() as session:
        entry = (await session.execute(
            select(HpsMapPool).where(HpsMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            out["status"]  = "not_found"
            out["message"] = f"Map {beatmap_id} not in HPS pool"
            return out

        _, reasons = hps_map_is_broken(entry)
        out["reasons"] = reasons

    data = await _retry(lambda: api_client.get_beatmap(beatmap_id))
    osu_bytes = await _retry(lambda: api_client.download_osu_file(beatmap_id))

    if not data and not osu_bytes:
        out["status"]  = "no_data"
        out["message"] = "API and CDN both unavailable"
        return out

    bset      = (data or {}).get("beatmapset") or {}
    sr        = float((data or {}).get("difficulty_rating") or 0) if data else 0.0
    bpm       = float((data or {}).get("bpm") or bset.get("bpm") or 0) if data else 0.0
    length    = int((data or {}).get("total_length") or (data or {}).get("hit_length") or 0) if data else 0
    ar        = float((data or {}).get("ar")       or 0) if data else 0.0
    od        = float((data or {}).get("accuracy") or 0) if data else 0.0
    cs        = float((data or {}).get("cs")       or 0) if data else 0.0
    max_combo = int((data or {}).get("max_combo")  or 0) if data else 0
    status    = ((bset.get("status") or (data or {}).get("status") or "ranked")).lower() if data else None

    osu_text = None
    if osu_bytes:
        try:
            osu_text = osu_bytes.decode("utf-8", errors="replace")
        except Exception:
            osu_text = None

    async with get_db_session() as session:
        entry = (await session.execute(
            select(HpsMapPool).where(HpsMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            out["status"] = "not_found"
            return out

        updated: list[str] = []

        if sr > 0 and (entry.star_rating or 0) != sr:
            entry.star_rating = sr; updated.append("star_rating")
        if bpm > 0 and (entry.bpm or 0) != bpm:
            entry.bpm = bpm; updated.append("bpm")
        if length > 0 and (entry.length or 0) != length:
            entry.length = length; updated.append("length")
        if ar and (entry.ar or 0) != ar:
            entry.ar = ar; updated.append("ar")
        if od and (entry.od or 0) != od:
            entry.od = od; updated.append("od")
        if cs and (entry.cs or 0) != cs:
            entry.cs = cs; updated.append("cs")
        if max_combo > 0 and (entry.max_combo or 0) != max_combo:
            entry.max_combo = max_combo; updated.append("max_combo")

        if data and bset:
            new_title   = bset.get("title")  or (data or {}).get("version")
            new_artist  = bset.get("artist")
            new_version = (data or {}).get("version")
            new_creator = bset.get("creator")
            if new_title   and entry.title   != new_title:   entry.title   = new_title;   updated.append("title")
            if new_artist  and entry.artist  != new_artist:  entry.artist  = new_artist;  updated.append("artist")
            if new_version and entry.version != new_version: entry.version = new_version; updated.append("version")
            if new_creator and entry.creator != new_creator: entry.creator = new_creator; updated.append("creator")

        try:
            profile = compute_hps_profile(
                osu_text,
                bpm=entry.bpm or bpm or 0,
                ar=entry.ar or ar or 0,
                od=entry.od or od or 0,
                length_s=entry.length or length or 0,
                star_rating=entry.star_rating or sr or 0,
                ranked_status=status or entry.ranked_status or "ranked",
            )
            apply_profile_to_entry(entry, profile)
            updated.append("profile")
        except Exception as e:
            logger.warning(f"refresh_hps_map({beatmap_id}): compute_hps_profile failed: {e}")

        if re_enable and not entry.enabled:
            entry.enabled = True
            updated.append("enabled")

        await session.commit()
        await session.refresh(entry)

        after_broken, _ = hps_map_is_broken(entry)

    out["updated"] = updated
    if not updated:
        out["status"]  = "no_data"
        out["message"] = "Nothing to update"
    elif after_broken:
        out["status"]  = "partial"
        out["message"] = f"Refreshed {len(updated)} field(s) but map still incomplete"
    else:
        out["status"]  = "ok"
        out["message"] = f"Refreshed {len(updated)} field(s)"
    return out


__all__ = [
    "add_map_to_hps_pool",
    "refresh_hps_map",
    "hps_map_is_broken",
    "apply_profile_to_entry",
]
