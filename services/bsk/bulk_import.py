"""
BSK map pool bulk importer.
Accepts a .zip of .osz files (or a single .osz), extracts .osu files,
parses them and adds maps to bsk_map_pool.

The importer is intentionally streaming-friendly: handlers may pass a file path
instead of in-memory bytes so several queued imports do not keep multi-GB
archives in RAM.
"""

import asyncio
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger("bsk.bulk_import")

# Caps sized for URL-imported multi-GB mappacks (the /import downloader
# allows 25 GB and single .7z packs are extracted + re-zipped before they
# reach here). MAX_OSZ_SIZE bounds peak RAM — _iter_osu_entries_from_archive
# reads one inner .osz fully into memory at a time, so it caps a single map,
# not the whole pack. MAX_TOTAL_UNCOMPRESSED / MAX_ZIP_ENTRIES are the
# pack-wide zip-bomb guards, raised to match the 25 GB download path.
MAX_ZIP_ENTRIES = 100_000
MAX_OSZ_SIZE = 1024 * 1024 * 1024            # 1 GB per inner .osz
MAX_OSU_SIZE = 16 * 1024 * 1024              # 16 MB per .osu (marathons)
MAX_OSU_FILES_PER_IMPORT = 20_000
MAX_TOTAL_UNCOMPRESSED = 30 * 1024 * 1024 * 1024  # 30 GB
MAX_COMPRESSION_RATIO = 100
BSK_BULK_API_DELAY_SECONDS = 0.12
BSK_BULK_API_RETRIES = 3
# Retry a row insert on transient SQLite write-lock contention. WAL +
# busy_timeout (see db.database) already absorb most of it; this is the
# last line of defence so a locked write never silently drops a map.
BSK_BULK_DB_RETRIES = 4
BSK_BULK_DB_RETRY_DELAY = 0.25  # seconds, multiplied by the attempt number


def _zip_ratio(info: zipfile.ZipInfo) -> float:
    return float(info.file_size) / max(float(info.compress_size), 1.0)


def _validate_zip_member(info: zipfile.ZipInfo, *, max_size: int, kind: str) -> None:
    if info.is_dir():
        return
    if info.file_size > max_size:
        raise ValueError(f"{kind} too large: {info.filename} ({info.file_size} bytes)")
    if _zip_ratio(info) > MAX_COMPRESSION_RATIO:
        raise ValueError(
            f"Suspicious compression ratio for {kind}: "
            f"{info.filename} ({_zip_ratio(info):.1f}x)"
        )


def _iter_limited_infos(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError(f"Too many files in archive: {len(infos)}")
    total = sum(i.file_size for i in infos if not i.is_dir())
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise ValueError(f"Archive uncompressed size too large: {total} bytes")
    return infos



async def _call_osu_api_limited(call, *args, **kwargs):
    """Call osu! API with a small delay and retry/backoff for bulk imports."""
    last_exc = None
    for attempt in range(BSK_BULK_API_RETRIES):
        if attempt > 0:
            await asyncio.sleep(min(2.0, 0.5 * (attempt + 1)))
        try:
            result = await call(*args, **kwargs)
            await asyncio.sleep(BSK_BULK_API_DELAY_SECONDS)
            return result
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            if "429" not in msg and "rate" not in msg and attempt >= 1:
                break
    if last_exc:
        raise last_exc
    return None


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
    """Return list of (filename, osu_text) from an .osz archive with size limits."""
    results = []
    try:
        with zipfile.ZipFile(io.BytesIO(osz_bytes)) as zf:
            for info in _iter_limited_infos(zf):
                name = info.filename
                if not name.endswith(".osu"):
                    continue
                _validate_zip_member(info, max_size=MAX_OSU_SIZE, kind=".osu")
                try:
                    raw = zf.read(info)
                    results.append((name, raw.decode("utf-8", errors="replace")))
                except Exception as e:
                    logger.debug(f"Failed to read {name}: {e}")
    except (zipfile.BadZipFile, ValueError) as e:
        logger.warning(f"Bad or unsafe .osz archive: {e}")
    return results


def _iter_osu_entries_from_osz_bytes(osz_name: str, osz_bytes: bytes):
    """Yield (osu_filename, osu_text) entries from one .osz bytes blob."""
    yield from _extract_osu_files_from_osz(osz_bytes)


def _iter_osu_entries_from_archive(path: str | os.PathLike):
    """Yield .osu entries from .zip-of-.osz, bare .osz, or zip with .osu files.

    This keeps only one inner .osz archive in memory at a time and rejects
    oversized / highly-compressed members before reading them.
    """
    path = str(path)
    yielded = 0
    try:
        with zipfile.ZipFile(path) as outer:
            found = False
            for info in _iter_limited_infos(outer):
                name = info.filename
                lname = name.lower()
                if lname.endswith(".osz"):
                    found = True
                    _validate_zip_member(info, max_size=MAX_OSZ_SIZE, kind=".osz")
                    for entry in _iter_osu_entries_from_osz_bytes(name, outer.read(info)):
                        yielded += 1
                        if yielded > MAX_OSU_FILES_PER_IMPORT:
                            raise ValueError(f"Too many .osu files in import: {yielded}")
                        yield entry
                elif lname.endswith(".osu"):
                    found = True
                    _validate_zip_member(info, max_size=MAX_OSU_SIZE, kind=".osu")
                    raw = outer.read(info)
                    yielded += 1
                    if yielded > MAX_OSU_FILES_PER_IMPORT:
                        raise ValueError(f"Too many .osu files in import: {yielded}")
                    yield name, raw.decode("utf-8", errors="replace")
            if not found:
                return
    except zipfile.BadZipFile:
        # Maybe it's a bare .osz file. Overall upload size was already capped by handler.
        size = os.path.getsize(path)
        if size > MAX_OSZ_SIZE:
            raise ValueError(f".osz too large: {size} bytes")
        with open(path, "rb") as f:
            for entry in _iter_osu_entries_from_osz_bytes(Path(path).name or "upload.osz", f.read()):
                yielded += 1
                if yielded > MAX_OSU_FILES_PER_IMPORT:
                    raise ValueError(f"Too many .osu files in import: {yielded}")
                yield entry


async def _insert_bsk_map(entry_kwargs: dict, result: dict):
    """Insert one BskMapPool row. Returns (status, entry):

      'added'     — committed; entry is the persisted row (for logging).
      'duplicate' — IntegrityError, a genuine constraint hit; not retryable.
      'locked'    — 'database is locked' survived every retry; entry is None.

    SQLite serialises writes, so under contention a commit can raise
    OperationalError('database is locked'). That's transient — retry with a
    short backoff — and must NOT be confused with a real IntegrityError.
    """
    from db.database import get_db_session
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import apply_to_entry
    from sqlalchemy.exc import IntegrityError, OperationalError

    for attempt in range(BSK_BULK_DB_RETRIES):
        try:
            async with get_db_session() as session:
                entry = BskMapPool(**entry_kwargs)
                apply_to_entry(entry, result)
                session.add(entry)
                await session.commit()
                return "added", entry
        except IntegrityError:
            return "duplicate", None
        except OperationalError as e:
            if "locked" in str(e).lower() and attempt < BSK_BULK_DB_RETRIES - 1:
                await asyncio.sleep(BSK_BULK_DB_RETRY_DELAY * (attempt + 1))
                continue
            logger.warning(
                f"BSK import: DB lock didn't clear after {attempt + 1} attempt(s): {e}"
            )
            return "locked", None
    return "locked", None


async def _import_osu_entries(osu_entries, osu_api_client) -> dict:
    """Process an iterable of (filename, osu_text)."""
    from db.database import get_db_session
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import analyze_map, apply_to_entry
    from sqlalchemy import select

    added = 0
    skipped = 0
    failed = 0
    errors = []
    seen_any = False
    seen_ids: set[int] = set()

    for osu_filename, osu_text in osu_entries:
        seen_any = True
        try:
            meta = _parse_osu_metadata(osu_text)
            mode = meta.get("Mode", "0")
            logger.info(f"BSK import: processing {osu_filename!r} mode={mode!r} keys={list(meta.keys())[:6]}")

            if mode != "0":
                logger.info(f"BSK import: skipping {osu_filename} — mode={mode!r}")
                skipped += 1
                continue

            beatmap_id = _beatmap_id_from_meta(meta, osu_filename)
            if not beatmap_id:
                logger.warning(
                    f"BSK import: skipping {osu_filename} — no BeatmapID "
                    f"(unsubmitted or guest difficulty)"
                )
                skipped += 1
                continue

            if beatmap_id in seen_ids:
                logger.info(f"BSK import: skipping {osu_filename} — duplicate in import id={beatmap_id}")
                skipped += 1
                continue
            seen_ids.add(beatmap_id)

            async with get_db_session() as session:
                existing = (await session.execute(
                    select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
                )).scalar_one_or_none()
                if existing:
                    logger.info(f"BSK import: skipping {osu_filename} — already exists id={beatmap_id}")
                    skipped += 1
                    continue

            title         = meta.get("Title") or meta.get("TitleUnicode") or "Unknown"
            artist        = meta.get("Artist") or meta.get("ArtistUnicode") or "Unknown"
            version       = meta.get("Version") or ""
            creator       = meta.get("Creator") or ""
            beatmapset_id = int(meta.get("BeatmapSetID") or 0)

            try:
                ar = float(meta.get("ApproachRate") or meta.get("OverallDifficulty") or 0)
                od = float(meta.get("OverallDifficulty") or 0)
                cs = float(meta.get("CircleSize") or 0)
                hp = float(meta.get("HPDrainRate") or 0)
            except ValueError:
                ar = od = cs = hp = 0.0

            sr = 0.0
            bpm = 0.0
            api_aim = api_speed = api_slider = api_speed_notes = None
            if beatmap_id and osu_api_client:
                try:
                    bmap_data = await _call_osu_api_limited(osu_api_client.get_beatmap, beatmap_id)
                    if bmap_data:
                        sr  = float(bmap_data.get("difficulty_rating") or 0)
                        bpm = float(bmap_data.get("bpm") or 0)
                        bset = bmap_data.get("beatmapset") or {}
                        if not beatmapset_id:
                            beatmapset_id = int(bmap_data.get("beatmapset_id") or bset.get("id") or 0)
                except Exception:
                    pass
                try:
                    attrs = await _call_osu_api_limited(osu_api_client.get_beatmap_attributes, beatmap_id)
                    if attrs:
                        api_aim         = attrs.get("aim_difficulty")
                        api_speed       = attrs.get("speed_difficulty")
                        api_slider      = attrs.get("slider_factor")
                        api_speed_notes = attrs.get("speed_note_count")
                except Exception:
                    pass

            result = analyze_map(
                osu_text,
                bpm=bpm, ar=ar, od=od,
                length_s=int(0),
                star_rating=sr,
                api_aim=float(api_aim or 0.0),
                api_speed=float(api_speed or 0.0),
            )
            length_from_parser = int(result["features"].get("duration_seconds") or 0)

            entry_kwargs = dict(
                beatmap_id=beatmap_id,
                beatmapset_id=beatmapset_id,
                title=title,
                artist=artist,
                version=version,
                creator=creator,
                star_rating=sr,
                bpm=bpm,
                length=length_from_parser,
                ar=ar, od=od, cs=cs, hp_drain=hp,
                api_aim_diff=api_aim,
                api_speed_diff=api_speed,
                api_slider_factor=api_slider,
                api_speed_note_count=api_speed_notes,
                enabled=True,
            )
            status, entry = await _insert_bsk_map(entry_kwargs, result)
            if status == "added":
                added += 1
                logger.info(
                    f"BSK bulk import: added {artist} - {title} [{version}] "
                    f"(id={beatmap_id} type={entry.map_type} "
                    f"a{entry.aim_stars}/s{entry.speed_stars}/"
                    f"c{entry.acc_stars}/n{entry.cons_stars})"
                )
            elif status == "duplicate":
                skipped += 1
                logger.info(
                    f"BSK import: skipped {osu_filename} — duplicate / DB constraint"
                )
            else:  # 'locked'
                skipped += 1
                logger.warning(
                    f"BSK import: skipped {osu_filename} — DB stayed locked, gave up"
                )

        except Exception as e:
            failed += 1
            errors.append(f"{osu_filename}: {e}")
            logger.warning(f"BSK bulk import failed for {osu_filename}: {e}")

    if not seen_any:
        return {"added": 0, "skipped": 0, "failed": 0, "errors": ["No .osz files found in archive"]}
    return {"added": added, "skipped": skipped, "failed": failed, "errors": errors[:5]}


async def import_from_file(
    file_path: str | os.PathLike,
    osu_api_client,
) -> dict:
    """Process a .zip/.osz file from disk without loading the whole upload into RAM."""
    return await _import_osu_entries(_iter_osu_entries_from_archive(file_path), osu_api_client)


async def import_from_zip(
    zip_bytes: bytes,
    osu_api_client,
) -> dict:
    """
    Process a .zip of .osz files (or a single .osz).
    Returns {added, skipped, failed, errors}.

    Kept for backwards compatibility. New bulk handlers should prefer
    import_from_file() to avoid keeping large uploads in memory.
    """
    return await _import_osu_entries(_iter_osu_entries_from_archive_bytes(zip_bytes), osu_api_client)


def _iter_osu_entries_from_archive_bytes(zip_bytes: bytes):
    """Bytes-backed compatibility iterator for tests/legacy callers."""
    yielded = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as outer:
            found = False
            for info in _iter_limited_infos(outer):
                name = info.filename
                lname = name.lower()
                if lname.endswith(".osz"):
                    found = True
                    _validate_zip_member(info, max_size=MAX_OSZ_SIZE, kind=".osz")
                    for entry in _iter_osu_entries_from_osz_bytes(name, outer.read(info)):
                        yielded += 1
                        if yielded > MAX_OSU_FILES_PER_IMPORT:
                            raise ValueError(f"Too many .osu files in import: {yielded}")
                        yield entry
                elif lname.endswith(".osu"):
                    found = True
                    _validate_zip_member(info, max_size=MAX_OSU_SIZE, kind=".osu")
                    raw = outer.read(info)
                    yielded += 1
                    if yielded > MAX_OSU_FILES_PER_IMPORT:
                        raise ValueError(f"Too many .osu files in import: {yielded}")
                    yield name, raw.decode("utf-8", errors="replace")
            if not found:
                return
    except zipfile.BadZipFile:
        if len(zip_bytes) > MAX_OSZ_SIZE:
            raise ValueError(f".osz too large: {len(zip_bytes)} bytes")
        for entry in _iter_osu_entries_from_osz_bytes("upload.osz", zip_bytes):
            yielded += 1
            if yielded > MAX_OSU_FILES_PER_IMPORT:
                raise ValueError(f"Too many .osu files in import: {yielded}")
            yield entry
