"""
DUEL map pool populator — fetches beatmaps from osu! API and adds them to duel_map_pool.

Phase 2: switched to the new skill-stars pipeline (`analyze_map` + per-skill
stars).  Old `_estimate_weights` is kept as a thin shim that uses the same
pipeline so `/duelrecalc` (which has no .osu file at hand) still works on
metadata-only data.
"""

import asyncio
from typing import Optional

from sqlalchemy import select
from db.database import get_db_session
from db.models.duel_map_pool import DuelMapPool
from utils.logger import get_logger

logger = get_logger("duel.map_pool")


# ─── Public pipeline ─────────────────────────────────────────────────────────

def analyze_map(
    osu_text: Optional[str],
    *,
    bpm: float,
    ar:  float,
    od:  float,
    length_s: int,
    star_rating: float,
    api_aim: float = 0.0,
    api_speed: float = 0.0,
) -> dict:
    """One-stop DUEL pipeline (shim).

    Real logic lives in `services.duel.duel_profile.compute_duel_profile`.
    This shim preserves the legacy import path used by `/duelrecalc`,
    `add_map_to_pool`, etc.
    """
    from services.duel.duel_profile import compute_duel_profile
    return compute_duel_profile(
        osu_text,
        bpm=bpm, ar=ar, od=od,
        length_s=length_s, star_rating=star_rating,
        api_aim=api_aim, api_speed=api_speed,
    )


def apply_to_entry(entry: DuelMapPool, result: dict) -> None:
    """Write an `analyze_map(...)` result onto a DuelMapPool ORM row.
    Caller is responsible for the session/commit."""
    f = result["features"]
    s = result["stars"]
    w = result["weights"]

    entry.aim_stars   = s["aim"]
    entry.speed_stars = s["speed"]
    entry.acc_stars   = s["acc"]
    entry.cons_stars  = s["cons"]

    entry.w_aim   = w["aim"]
    entry.w_speed = w["speed"]
    entry.w_acc   = w["acc"]
    entry.w_cons  = w["cons"]

    entry.map_type = result["map_type"]

    # Pattern features (only overwrite when we actually re-parsed)
    if f.get("note_count", 0):
        # aim
        entry.f_jump_density = f.get("jump_density")
        entry.f_jump_vel     = f.get("avg_jump_velocity")
        entry.f_back_forth   = f.get("back_forth_ratio")
        entry.f_angle_var    = f.get("angle_variance")
        entry.f_flow_break   = f.get("flow_break_density")
        # speed
        entry.f_burst         = f.get("burst_density")
        entry.f_stream        = f.get("full_stream_density")
        entry.f_death_stream  = f.get("death_stream_density")
        entry.f_bpm_rel_speed = f.get("bpm_rel_speed")
        # acc
        entry.f_subdiv_entropy     = f.get("subdiv_entropy")
        entry.f_polyrhythm_density = f.get("polyrhythm_density")
        entry.f_off_beat_ratio     = f.get("off_beat_ratio")
        entry.f_jack_density       = f.get("jack_density")
        entry.f_slider_tail_demand = f.get("slider_tail_demand")
        entry.f_sv_var             = f.get("sv_variance")
        entry.f_slider_density     = f.get("slider_density")
        # OD demand is computed in the formula step — store the raw value too
        nc  = f.get("note_count", 0) or 0
        dur = max(f.get("duration_seconds", 1) or 1, 1)
        nps_n = min((nc / dur) / 8.0, 1.0)
        od_eff = max(0.0, ((entry.od or 0) - 5.0) / 5.0) if entry.od else 0.0
        entry.f_od_demand = round(od_eff * (0.4 + 0.6 * nps_n), 4)
        # cons
        entry.f_density_var      = f.get("density_variance")
        entry.f_intensity_floor  = f.get("intensity_floor")
        entry.f_pattern_repeat   = f.get("pattern_repetition")
        # general
        entry.f_rhythm_complexity = f.get("rhythm_complexity")
        entry.f_note_count        = f.get("note_count")
        entry.f_duration          = f.get("duration_seconds")


# ─── Legacy shim ─────────────────────────────────────────────────────────────

def _estimate_weights(
    bpm: float, ar: float, od: float, length: int,
    features: dict | None = None,
    api_aim: float = 0.0, api_speed: float = 0.0,
    api_slider_factor: float = 1.0,                # accepted but unused now
) -> dict:
    """LEGACY callers (e.g. /duelrecalc) — return share-weights only.

    Internally this routes through the new intrinsic+softmax path so the old
    and new code agree on the share weights they produce."""
    from services.duel.osu_parser import (
        compute_skill_intrinsics, stars_to_weights,
    )
    feats = features or {"note_count": 0, "duration_seconds": length or 0}
    intr  = compute_skill_intrinsics(feats, bpm=bpm, ar=ar, od=od, length_s=length or 0)

    # Treat intrinsics as fake-stars and softmax — same path as the legacy
    # `weights_from_features` shim in osu_parser.py.
    fake_stars = {k: v * 10.0 for k, v in intr.items()}
    if api_aim > 0:
        fake_stars["aim"]   = 0.6 * fake_stars["aim"]   + 0.4 * api_aim
    if api_speed > 0:
        fake_stars["speed"] = 0.6 * fake_stars["speed"] + 0.4 * api_speed
    return stars_to_weights(fake_stars, temperature=2.0)


def _map_type(weights: dict) -> str:
    return max(weights, key=weights.get)


# ─── Map ingestion ───────────────────────────────────────────────────────────

async def add_map_to_pool(api_client, beatmap_id: int) -> Optional[DuelMapPool]:
    """Fetch beatmap from osu! API + .osu file, run the new analyzer, persist."""
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

    # Download .osu for deep pattern analysis
    osu_text = None
    osu_bytes = await api_client.download_osu_file(beatmap_id)
    if osu_bytes:
        try:
            osu_text = osu_bytes.decode("utf-8", errors="replace")
        except Exception:
            osu_text = None

    result = analyze_map(
        osu_text,
        bpm=bpm, ar=ar, od=od, length_s=length, star_rating=sr,
        api_aim=float(api_aim or 0.0), api_speed=float(api_speed or 0.0),
    )

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
        ar=ar, od=od, cs=cs, hp_drain=hp_drain,
        api_aim_diff=api_aim,
        api_speed_diff=api_speed,
        api_slider_factor=api_slider,
        api_speed_note_count=api_speed_notes,
        enabled=True,
    )
    apply_to_entry(entry, result)

    async with get_db_session() as session:
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

    logger.info(
        f"Added map {beatmap_id} '{entry.title}' "
        f"({sr}★ → aim {entry.aim_stars} / speed {entry.speed_stars} / "
        f"acc {entry.acc_stars} / cons {entry.cons_stars}, type={entry.map_type}) to DUEL pool"
    )
    return entry


async def search_and_populate(
    api_client,
    sr_min: float = 3.0,
    sr_max: float = 7.0,
    target_count: int = 50,
) -> int:
    """Experimental: search ranked maps by SR and populate pool."""
    added = 0
    cursor = None

    while added < target_count:
        params = {
            "mode":   "osu",
            "status": "ranked",
            "sort":   "difficulty_rating_asc",
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

    logger.info(f"DUEL pool populated: {added} maps added")
    return added


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
    if entry.api_aim_diff is None and entry.api_speed_diff is None:
        reasons.append("no_api_attrs")
    if entry.f_note_count is None or (entry.f_note_count or 0) == 0:
        reasons.append("no_features")
    if not entry.title or entry.title == "Unknown":
        reasons.append("no_metadata")
    return (bool(reasons), reasons)


async def refresh_map(api_client, beatmap_id: int, *, re_enable: bool = True) -> dict:
    """
    Re-pull metadata + .osu file for an existing pool entry, recompute features,
    weights and skill-stars, and optionally re-enable the map.

    Returns a status dict:
      {
        'beatmap_id': int,
        'status':     'ok' | 'not_found' | 'no_data' | 'partial' | 'error',
        'reasons':    list[str]   — what was broken before
        'updated':    list[str]   — fields actually rewritten
        'message':    str         — short human-readable summary
      }

    `partial` means we touched something but the map still looks broken
    afterwards (e.g. API gave SR but .osu CDN refused to serve the file).
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

    # Fetch with retry — flaky CDN/API is the main cause of broken pool entries.
    data = await _retry(lambda: api_client.get_beatmap(beatmap_id))
    attrs = await _retry(lambda: api_client.get_beatmap_attributes(beatmap_id))
    osu_bytes = await _retry(lambda: api_client.download_osu_file(beatmap_id))

    if not data and not attrs and not osu_bytes:
        out["status"]  = "no_data"
        out["message"] = "API and CDN both unavailable"
        return out

    bset = (data or {}).get("beatmapset") or {}
    sr   = float((data or {}).get("difficulty_rating") or 0) if data else 0.0
    bpm  = float((data or {}).get("bpm")          or bset.get("bpm")  or 0) if data else 0.0
    length = int((data or {}).get("total_length") or (data or {}).get("hit_length") or 0) if data else 0
    ar   = float((data or {}).get("ar")       or 0) if data else 0.0
    od   = float((data or {}).get("accuracy") or 0) if data else 0.0
    cs   = float((data or {}).get("cs")       or 0) if data else 0.0
    hp_drain = float((data or {}).get("drain") or 0) if data else 0.0

    api_aim    = (attrs or {}).get("aim_difficulty")
    api_speed  = (attrs or {}).get("speed_difficulty")
    api_slider = (attrs or {}).get("slider_factor")
    api_speed_notes = (attrs or {}).get("speed_note_count")

    osu_text = None
    if osu_bytes:
        try:
            osu_text = osu_bytes.decode("utf-8", errors="replace")
        except Exception:
            osu_text = None

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

        # Re-run feature extraction + skill stars whenever we can.
        try:
            sr_for_analyzer = entry.star_rating or sr or 0
            result = analyze_map(
                osu_text,
                bpm=entry.bpm or bpm or 0,
                ar=entry.ar or ar or 0,
                od=entry.od or od or 0,
                length_s=entry.length or length or 0,
                star_rating=sr_for_analyzer,
                api_aim=float(api_aim or entry.api_aim_diff or 0.0),
                api_speed=float(api_speed or entry.api_speed_diff or 0.0),
            )
            apply_to_entry(entry, result)
            updated.append("features+stars")
        except Exception as e:
            logger.warning(f"refresh_map({beatmap_id}): analyze_map failed: {e}")

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
