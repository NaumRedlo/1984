"""
DUEL map pool bulk importer.
Accepts a .zip of .osz files (or a single .osz), extracts .osu files,
parses them and adds maps to duel_map_pool.

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

logger = get_logger("duel.bulk_import")

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
DUEL_BULK_API_DELAY_SECONDS = 0.12
DUEL_BULK_API_RETRIES = 3
# Retry a row insert on transient SQLite write-lock contention. WAL +
# busy_timeout (see db.database) already absorb most of it; this is the
# last line of defence so a locked write never silently drops a map.
DUEL_BULK_DB_RETRIES = 4
DUEL_BULK_DB_RETRY_DELAY = 0.25  # seconds, multiplied by the attempt number


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
    for attempt in range(DUEL_BULK_API_RETRIES):
        if attempt > 0:
            await asyncio.sleep(min(2.0, 0.5 * (attempt + 1)))
        try:
            result = await call(*args, **kwargs)
            await asyncio.sleep(DUEL_BULK_API_DELAY_SECONDS)
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


async def _insert_duel_map(entry_kwargs: dict):
    """Insert one DuelMapPool row. Returns (status, entry):

      'added'     — committed; entry is the persisted row (for logging).
      'duplicate' — IntegrityError, a genuine constraint hit; not retryable.
      'locked'    — 'database is locked' survived every retry; entry is None.

    SQLite serialises writes, so under contention a commit can raise
    OperationalError('database is locked'). That's transient — retry with a
    short backoff — and must NOT be confused with a real IntegrityError.
    """
    from db.database import get_db_session
    from db.models.duel_map_pool import DuelMapPool
    from sqlalchemy.exc import IntegrityError, OperationalError

    for attempt in range(DUEL_BULK_DB_RETRIES):
        try:
            async with get_db_session() as session:
                entry = DuelMapPool(**entry_kwargs)
                session.add(entry)
                await session.commit()
                return "added", entry
        except IntegrityError:
            return "duplicate", None
        except OperationalError as e:
            if "locked" in str(e).lower() and attempt < DUEL_BULK_DB_RETRIES - 1:
                await asyncio.sleep(DUEL_BULK_DB_RETRY_DELAY * (attempt + 1))
                continue
            logger.warning(
                f"DUEL import: DB lock didn't clear after {attempt + 1} attempt(s): {e}"
            )
            return "locked", None
    return "locked", None


async def _import_osu_entries(osu_entries, osu_api_client) -> dict:
    """Process an iterable of (filename, osu_text)."""
    from db.database import get_db_session
    from db.models.duel_map_pool import DuelMapPool
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
            logger.info(f"DUEL import: processing {osu_filename!r} mode={mode!r} keys={list(meta.keys())[:6]}")

            if mode != "0":
                logger.info(f"DUEL import: skipping {osu_filename} — mode={mode!r}")
                skipped += 1
                continue

            beatmap_id = _beatmap_id_from_meta(meta, osu_filename)
            if not beatmap_id:
                logger.warning(
                    f"DUEL import: skipping {osu_filename} — no BeatmapID "
                    f"(unsubmitted or guest difficulty)"
                )
                skipped += 1
                continue

            if beatmap_id in seen_ids:
                logger.info(f"DUEL import: skipping {osu_filename} — duplicate in import id={beatmap_id}")
                skipped += 1
                continue
            seen_ids.add(beatmap_id)

            async with get_db_session() as session:
                existing = (await session.execute(
                    select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
                )).scalar_one_or_none()
                if existing:
                    logger.info(f"DUEL import: skipping {osu_filename} — already exists id={beatmap_id}")
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
            length = 0
            max_combo = 0
            api_aim = api_speed = api_slider = api_speed_notes = None
            if beatmap_id and osu_api_client:
                try:
                    bmap_data = await _call_osu_api_limited(osu_api_client.get_beatmap, beatmap_id)
                    if bmap_data:
                        sr  = float(bmap_data.get("difficulty_rating") or 0)
                        bpm = float(bmap_data.get("bpm") or 0)
                        length = int(bmap_data.get("total_length") or bmap_data.get("hit_length") or 0)
                        max_combo = int(bmap_data.get("max_combo") or 0)
                        # API stats are authoritative — override the .osu metadata.
                        ar = float(bmap_data.get("ar") or ar)
                        od = float(bmap_data.get("accuracy") or od)
                        cs = float(bmap_data.get("cs") or cs)
                        hp = float(bmap_data.get("drain") or hp)
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

            entry_kwargs = dict(
                beatmap_id=beatmap_id,
                beatmapset_id=beatmapset_id,
                title=title,
                artist=artist,
                version=version,
                creator=creator,
                star_rating=sr,
                bpm=bpm,
                length=length,
                max_combo=max_combo,
                ar=ar, od=od, cs=cs, hp_drain=hp,
                api_aim_diff=api_aim,
                api_speed_diff=api_speed,
                api_slider_factor=api_slider,
                api_speed_note_count=api_speed_notes,
                enabled=True,
            )
            status, entry = await _insert_duel_map(entry_kwargs)
            if status == "added":
                added += 1
                logger.info(
                    f"DUEL bulk import: added {artist} - {title} [{version}] "
                    f"(id={beatmap_id} {sr:.2f}★ {length}s combo={max_combo})"
                )
            elif status == "duplicate":
                skipped += 1
                logger.info(
                    f"DUEL import: skipped {osu_filename} — duplicate / DB constraint"
                )
            else:  # 'locked'
                skipped += 1
                logger.warning(
                    f"DUEL import: skipped {osu_filename} — DB stayed locked, gave up"
                )

        except Exception as e:
            failed += 1
            errors.append(f"{osu_filename}: {e}")
            logger.warning(f"DUEL bulk import failed for {osu_filename}: {e}")

    if not seen_any:
        return {"added": 0, "skipped": 0, "failed": 0, "errors": ["No .osz files found in archive"]}
    return {"added": added, "skipped": skipped, "failed": failed, "errors": errors[:5]}


async def import_from_file(
    file_path: str | os.PathLike,
    osu_api_client,
) -> dict:
    """Process a .zip/.osz file from disk without loading the whole upload into RAM."""
    return await _import_osu_entries(_iter_osu_entries_from_archive(file_path), osu_api_client)


def libarchive_available() -> bool:
    """True if the libarchive-c binding imports and its shared lib (libarchive.so,
    near-ubiquitous) loads. The streaming .7z path depends on it; callers fall
    back to extract-and-rezip when it is absent."""
    try:
        import libarchive  # noqa: F401
        import libarchive.ffi  # noqa: F401  — forces the libarchive.so load
        return True
    except Exception:
        return False


def _iter_osu_entries_from_7z(path: str | os.PathLike):
    """Yield (osu_filename, osu_text) from a .7z by streaming each inner .osz out
    one at a time via libarchive — no on-disk extraction, no re-zip.

    Only a single inner .osz is held in memory at a time (capped by
    MAX_OSZ_SIZE), so peak *disk* stays at the size of the .7z itself. That is
    what lets a multi-GB pack import on a small VPS, where the extract-then-rezip
    path needs roughly 3x the archive size free.
    """
    import libarchive  # lazy: optional dependency, probed via libarchive_available()

    path = str(path)
    yielded = 0
    with libarchive.file_reader(path) as arch:
        for entry in arch:
            if not entry.isfile:
                continue
            name = entry.pathname or ""
            lname = name.lower()
            if lname.endswith(".osz"):
                buf = bytearray()
                for blk in entry.get_blocks():
                    buf += blk
                    if len(buf) > MAX_OSZ_SIZE:
                        break
                if len(buf) > MAX_OSZ_SIZE:
                    logger.warning(f".7z import: skipping oversized .osz {name}")
                    continue
                for osu_entry in _extract_osu_files_from_osz(bytes(buf)):
                    yielded += 1
                    if yielded > MAX_OSU_FILES_PER_IMPORT:
                        raise ValueError(f"Too many .osu files in import: {yielded}")
                    yield osu_entry
            elif lname.endswith(".osu"):
                buf = bytearray()
                for blk in entry.get_blocks():
                    buf += blk
                    if len(buf) > MAX_OSU_SIZE:
                        break
                if len(buf) > MAX_OSU_SIZE:
                    logger.warning(f".7z import: skipping oversized .osu {name}")
                    continue
                yielded += 1
                if yielded > MAX_OSU_FILES_PER_IMPORT:
                    raise ValueError(f"Too many .osu files in import: {yielded}")
                yield name, bytes(buf).decode("utf-8", errors="replace")


async def import_from_7z(
    file_path: str | os.PathLike,
    osu_api_client,
) -> dict:
    """Stream-import a single .7z without extracting it to disk.

    Requires the libarchive-c binding; raises RuntimeError('libarchive-unavailable')
    when it is missing so the caller can fall back / show an actionable hint.
    """
    if not libarchive_available():
        raise RuntimeError("libarchive-unavailable")
    return await _import_osu_entries(_iter_osu_entries_from_7z(file_path), osu_api_client)
