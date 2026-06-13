"""
DUEL map pool populator — fetches beatmaps from osu! API and adds them to duel_map_pool.

Only objective osu! metadata is stored (star_rating, bpm, length, max_combo,
CS/AR/OD/HP, plus the official aim/speed sub-ratings). The per-axis skill
classifier was removed — `star_rating` is the single difficulty signal.
"""

import asyncio
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.duel_map_pool import DuelMapPool
from utils.logger import get_logger

logger = get_logger("duel.map_pool")


# ─── Map ingestion ───────────────────────────────────────────────────────────

async def add_map_to_pool(api_client, beatmap_id: int) -> Optional[DuelMapPool]:
    """Fetch beatmap metadata from osu! API and persist objective stats."""
    async with get_db_session() as session:
        existing = (await session.execute(
            select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if existing:
            logger.info(f"Map {beatmap_id} already in pool")
            return None

    data = await api_client.get_beatmap(beatmap_id)
    if not data:
        logger.warning(f"Beatmap {beatmap_id} not found in osu! API")
        return None

    bset = data.get("beatmapset") or {}
    bpm  = float(data.get("bpm")        or bset.get("bpm")        or 0)
    ar   = float(data.get("ar")         or 0)
    od   = float(data.get("accuracy")   or 0)
    cs   = float(data.get("cs")         or 0)
    hp_drain = float(data.get("drain")  or 0)
    length   = int(data.get("total_length") or data.get("hit_length") or 0)
    max_combo = int(data.get("max_combo") or 0)
    sr       = float(data.get("difficulty_rating") or 0)

    # API difficulty attributes (absolute aim/speed scales)
    api_aim = api_speed = api_slider = api_speed_notes = None
    try:
        attrs = await api_client.get_beatmap_attributes(beatmap_id)
        if attrs:
            api_aim         = attrs.get("aim_difficulty")
            api_speed       = attrs.get("speed_difficulty")
            api_slider      = attrs.get("slider_factor")
            api_speed_notes = attrs.get("speed_note_count")
    except Exception:
        pass

    entry = DuelMapPool(
        beatmap_id=beatmap_id,
        beatmapset_id=int(data.get("beatmapset_id") or bset.get("id") or 0),
        title=bset.get("title") or data.get("version") or "Unknown",
        artist=bset.get("artist") or "Unknown",
        version=data.get("version") or "",
        creator=bset.get("creator"),
        star_rating=sr,
        bpm=bpm,
        length=length,
        max_combo=max_combo,
        ar=ar, od=od, cs=cs, hp_drain=hp_drain,
        api_aim_diff=api_aim,
        api_speed_diff=api_speed,
        api_slider_factor=api_slider,
        api_speed_note_count=api_speed_notes,
        enabled=True,
    )

    async with get_db_session() as session:
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

    logger.info(
        f"Added map {beatmap_id} '{entry.title}' "
        f"({sr:.2f}★ {length}s combo={max_combo}) to DUEL pool"
    )
    return entry


# ─── Map refresh / repair ────────────────────────────────────────────────────

async def _retry(coro_factory, attempts: int = 3, delay: float = 0.6):
    """Call coro_factory() up to N times, returning the first non-None result.
    `coro_factory` is a zero-arg callable so we can await a fresh coroutine each
    attempt (re-using a coroutine raises RuntimeError)."""
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


def map_is_broken(entry: DuelMapPool) -> tuple[bool, list[str]]:
    """Return (is_broken, reasons). 'broken' means we should try to refresh."""
    reasons: list[str] = []
    if not entry.star_rating or entry.star_rating <= 0:
        reasons.append("sr=0")
    if not entry.length or entry.length <= 0:
        reasons.append("no_length")
    if not entry.title or entry.title == "Unknown":
        reasons.append("no_metadata")
    return (bool(reasons), reasons)


async def refresh_map(api_client, beatmap_id: int, *, re_enable: bool = True) -> dict:
    """
    Re-pull objective metadata for an existing pool entry and optionally
    re-enable the map.

    Returns a status dict:
      {
        'beatmap_id': int,
        'status':     'ok' | 'not_found' | 'no_data' | 'partial' | 'error',
        'reasons':    list[str]   — what was broken before
        'updated':    list[str]   — fields actually rewritten
        'message':    str         — short human-readable summary
      }

    `partial` means we touched something but the map still looks broken
    afterwards (e.g. the API stayed flaky and never returned a star rating).
    """
    out: dict = {"beatmap_id": beatmap_id, "status": "error", "reasons": [], "updated": [], "message": ""}

    async with get_db_session() as session:
        entry = (await session.execute(
            select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            out["status"]  = "not_found"
            out["message"] = f"Map {beatmap_id} not in pool"
            return out

        before_broken, reasons = map_is_broken(entry)
        out["reasons"] = reasons

    # Fetch with retry — a flaky API is the main cause of broken pool entries.
    data = await _retry(lambda: api_client.get_beatmap(beatmap_id))
    attrs = await _retry(lambda: api_client.get_beatmap_attributes(beatmap_id))

    if not data and not attrs:
        out["status"]  = "no_data"
        out["message"] = "osu! API unavailable"
        return out

    bset = (data or {}).get("beatmapset") or {}
    sr   = float((data or {}).get("difficulty_rating") or 0) if data else 0.0
    bpm  = float((data or {}).get("bpm")          or bset.get("bpm")  or 0) if data else 0.0
    length = int((data or {}).get("total_length") or (data or {}).get("hit_length") or 0) if data else 0
    max_combo = int((data or {}).get("max_combo") or 0) if data else 0
    ar   = float((data or {}).get("ar")       or 0) if data else 0.0
    od   = float((data or {}).get("accuracy") or 0) if data else 0.0
    cs   = float((data or {}).get("cs")       or 0) if data else 0.0
    hp_drain = float((data or {}).get("drain") or 0) if data else 0.0

    api_aim    = (attrs or {}).get("aim_difficulty")
    api_speed  = (attrs or {}).get("speed_difficulty")
    api_slider = (attrs or {}).get("slider_factor")
    api_speed_notes = (attrs or {}).get("speed_note_count")

    async with get_db_session() as session:
        entry = (await session.execute(
            select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            out["status"] = "not_found"
            return out

        updated: list[str] = []

        # Refresh metadata only if the API gave us a non-zero value (don't
        # blank out previously-good fields when the API is misbehaving).
        if sr > 0 and (entry.star_rating or 0) != sr:
            entry.star_rating = sr
            updated.append("star_rating")
        if bpm > 0 and (entry.bpm or 0) != bpm:
            entry.bpm = bpm
            updated.append("bpm")
        if length > 0 and (entry.length or 0) != length:
            entry.length = length
            updated.append("length")
        if max_combo > 0 and (entry.max_combo or 0) != max_combo:
            entry.max_combo = max_combo
            updated.append("max_combo")
        if ar and (entry.ar or 0) != ar:
            entry.ar = ar; updated.append("ar")
        if od and (entry.od or 0) != od:
            entry.od = od; updated.append("od")
        if cs and (entry.cs or 0) != cs:
            entry.cs = cs; updated.append("cs")
        if hp_drain and (entry.hp_drain or 0) != hp_drain:
            entry.hp_drain = hp_drain; updated.append("hp_drain")

        if api_aim is not None:
            entry.api_aim_diff = api_aim;       updated.append("api_aim_diff")
        if api_speed is not None:
            entry.api_speed_diff = api_speed;   updated.append("api_speed_diff")
        if api_slider is not None:
            entry.api_slider_factor = api_slider; updated.append("api_slider_factor")
        if api_speed_notes is not None:
            entry.api_speed_note_count = api_speed_notes; updated.append("api_speed_note_count")

        # Title / artist / version often arrive empty when the original add
        # raced the API — refresh them when we have something better.
        if data and bset:
            new_title  = bset.get("title")  or (data or {}).get("version")
            new_artist = bset.get("artist")
            new_version = (data or {}).get("version")
            new_creator = bset.get("creator")
            if new_title  and entry.title  != new_title:  entry.title  = new_title;  updated.append("title")
            if new_artist and entry.artist != new_artist: entry.artist = new_artist; updated.append("artist")
            if new_version and entry.version != new_version: entry.version = new_version; updated.append("version")
            if new_creator and entry.creator != new_creator: entry.creator = new_creator; updated.append("creator")

        if re_enable and not entry.enabled:
            entry.enabled = True
            updated.append("enabled")

        await session.commit()
        await session.refresh(entry)

        after_broken, _ = map_is_broken(entry)

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
