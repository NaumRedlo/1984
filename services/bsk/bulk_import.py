"""
BSK map pool bulk importer.
Accepts a .zip of .osz files (or a single .osz), extracts .osu files,
parses them and adds maps to bsk_map_pool.
"""

import io
import re
import zipfile
from typing import Optional

from utils.logger import get_logger

logger = get_logger("bsk.bulk_import")


def _parse_osu_metadata(osu_text: str) -> dict:
    """Extract General/Metadata/Difficulty sections from .osu text."""
    result = {}
    current_section = None
    for line in osu_text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if current_section in ("General", "Metadata", "Difficulty"):
            result[key] = value
    return result


def _beatmap_id_from_meta(meta: dict, filename: str) -> Optional[int]:
    """Try to get beatmap_id from metadata or filename."""
    bid = meta.get("BeatmapID")
    if bid and bid.isdigit() and int(bid) > 0:
        return int(bid)
    # Filename pattern: "artist - title (mapper) [diff].osu"
    # beatmapset folders are named "beatmapset_id artist - title"
    m = re.search(r"^\d+", filename)
    if m:
        return int(m.group())
    return None


def _extract_osu_files_from_osz(osz_bytes: bytes) -> list[tuple[str, str]]:
    """Return list of (filename, osu_text) from an .osz archive."""
    results = []
    try:
        with zipfile.ZipFile(io.BytesIO(osz_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith(".osu"):
                    try:
                        raw = zf.read(name)
                        results.append((name, raw.decode("utf-8", errors="replace")))
                    except Exception as e:
                        logger.debug(f"Failed to read {name}: {e}")
    except zipfile.BadZipFile as e:
        logger.warning(f"Bad .osz archive: {e}")
    return results


async def import_from_zip(
    zip_bytes: bytes,
    osu_api_client,
) -> dict:
    """
    Process a .zip of .osz files (or a single .osz).
    Returns {added, skipped, failed, errors}.
    """
    from db.database import get_db_session
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.osu_parser import extract_features, weights_from_features, map_type_from_weights
    from sqlalchemy import select

    added = 0
    skipped = 0
    failed = 0
    errors = []

    # Collect all .osu texts: support both .zip-of-.osz and bare .osz
    osz_files: list[tuple[str, bytes]] = []

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as outer:
            for name in outer.namelist():
                if name.lower().endswith(".osz"):
                    osz_files.append((name, outer.read(name)))
                elif name.lower().endswith(".osu"):
                    # Bare .osu inside a zip (unusual but handle it)
                    raw = outer.read(name)
                    osz_files.append((name + "/.osu_direct", raw))
    except zipfile.BadZipFile:
        # Maybe it's a bare .osz
        osz_files.append(("upload.osz", zip_bytes))

    if not osz_files:
        return {"added": 0, "skipped": 0, "failed": 0, "errors": ["No .osz files found in archive"]}

    for osz_name, osz_bytes in osz_files:
        # Handle bare .osu_direct marker
        if osz_name.endswith("/.osu_direct"):
            osu_entries = [(osz_name.replace("/.osu_direct", ""), osz_bytes.decode("utf-8", errors="replace"))]
        else:
            osu_entries = _extract_osu_files_from_osz(osz_bytes)

        for osu_filename, osu_text in osu_entries:
            try:
                meta = _parse_osu_metadata(osu_text)
                mode = meta.get("Mode", "0")
                logger.info(f"BSK import: processing {osu_filename!r} mode={mode!r} keys={list(meta.keys())[:6]}")

                # Skip non-standard modes
                if mode != "0":
                    logger.info(f"BSK import: skipping {osu_filename} — mode={mode!r}")
                    skipped += 1
                    continue

                beatmap_id = _beatmap_id_from_meta(meta, osu_filename)

                # Check existing
                async with get_db_session() as session:
                    if beatmap_id:
                        existing = (await session.execute(
                            select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
                        )).scalar_one_or_none()
                        if existing:
                            logger.info(f"BSK import: skipping {osu_filename} — already exists id={beatmap_id}")
                            skipped += 1
                            continue

                # Parse features from .osu
                features = extract_features(osu_text)

                # Get metadata from .osu file itself (no API call needed)
                title = meta.get("Title") or meta.get("TitleUnicode") or "Unknown"
                artist = meta.get("Artist") or meta.get("ArtistUnicode") or "Unknown"
                version = meta.get("Version") or ""
                creator = meta.get("Creator") or ""
                beatmapset_id = int(meta.get("BeatmapSetID") or 0)

                # Difficulty params from .osu
                try:
                    ar = float(meta.get("ApproachRate") or meta.get("OverallDifficulty") or 0)
                    od = float(meta.get("OverallDifficulty") or 0)
                    cs = float(meta.get("CircleSize") or 0)
                    hp = float(meta.get("HPDrainRate") or 0)
                except ValueError:
                    ar = od = cs = hp = 0.0

                # BPM: rough estimate from duration and note count
                bpm = 0.0
                dur = features.get("duration_seconds", 0)

                # Try to get SR and BPM from API if we have beatmap_id
                sr = 0.0
                if beatmap_id and osu_api_client:
                    try:
                        bmap_data = await osu_api_client.get_beatmap(beatmap_id)
                        if bmap_data:
                            sr = float(bmap_data.get("difficulty_rating") or 0)
                            bpm = float(bmap_data.get("bpm") or 0)
                            bset = bmap_data.get("beatmapset") or {}
                            if not beatmapset_id:
                                beatmapset_id = int(bmap_data.get("beatmapset_id") or bset.get("id") or 0)
                    except Exception:
                        pass

                # Fetch osu! API difficulty attributes
                api_aim = api_speed = api_slider = api_speed_notes = None
                if beatmap_id and osu_api_client:
                    try:
                        attrs = await osu_api_client.get_beatmap_attributes(beatmap_id)
                        if attrs:
                            api_aim         = attrs.get("aim_difficulty")
                            api_speed       = attrs.get("speed_difficulty")
                            api_slider      = attrs.get("slider_factor")
                            api_speed_notes = attrs.get("speed_note_count")
                    except Exception:
                        pass

                weights = weights_from_features(
                    features, bpm=bpm, ar=ar, od=od,
                    api_aim=api_aim or 0.0,
                    api_speed=api_speed or 0.0,
                    api_slider_factor=api_slider if api_slider is not None else 1.0,
                )
                map_type = map_type_from_weights(weights)

                async with get_db_session() as session:
                    entry = BskMapPool(
                        beatmap_id=beatmap_id or 0,
                        beatmapset_id=beatmapset_id,
                        title=title,
                        artist=artist,
                        version=version,
                        creator=creator,
                        star_rating=sr,
                        bpm=bpm,
                        length=dur,
                        ar=ar,
                        od=od,
                        cs=cs,
                        w_aim=weights["aim"],
                        w_speed=weights["speed"],
                        w_acc=weights["acc"],
                        w_cons=weights["cons"],
                        map_type=map_type,
                        # osu! API attributes
                        api_aim_diff=api_aim,
                        api_speed_diff=api_speed,
                        api_slider_factor=api_slider,
                        api_speed_note_count=api_speed_notes,
                        # parsed pattern features
                        f_burst=features.get("burst_density"),
                        f_stream=features.get("full_stream_density"),
                        f_death_stream=features.get("death_stream_density"),
                        f_jump_vel=features.get("avg_jump_velocity"),
                        f_back_forth=features.get("back_forth_ratio"),
                        f_angle_var=features.get("angle_variance"),
                        f_sv_var=features.get("sv_variance"),
                        f_density_var=features.get("density_variance"),
                        enabled=True,
                    )
                    session.add(entry)
                    try:
                        await session.commit()
                        added += 1
                        logger.info(f"BSK bulk import: added {artist} - {title} [{version}] (id={beatmap_id})")
                    except Exception as e:
                        await session.rollback()
                        skipped += 1
                        logger.info(f"BSK import: skipped {osu_filename} (constraint): {e}")

            except Exception as e:
                failed += 1
                errors.append(f"{osu_filename}: {e}")
                logger.warning(f"BSK bulk import failed for {osu_filename}: {e}")

    return {"added": added, "skipped": skipped, "failed": failed, "errors": errors[:5]}
