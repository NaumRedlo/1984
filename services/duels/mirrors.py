import hashlib
import io
import json
import os
import time
import zipfile
from urllib.parse import quote
from typing import Any, Awaitable, Callable, Optional

from services.duels.osu_file import FILE_MOST

USER_AGENT = "project1984-duels/1.0 (+https://github.com/NaumRedlo/1984)"
FILES = (
    ("osu.direct", "https://osu.direct/api/osu/{beatmap_id}"),
    ("catboy.best", "https://catboy.best/osu/{beatmap_id}"),
)
MIRRORS = (
    ("osu.direct", "https://osu.direct/api/d/{set_id}?noVideo=1"),
    ("catboy.best", "https://catboy.best/d/{set_id}n"),
    ("beatconnect.io", "https://beatconnect.io/b/{set_id}/"),
    ("nekoha.moe", "https://mirror.nekoha.moe/api4/download/{set_id}"),
    ("sayobot.cn", "https://dl.sayobot.cn/beatmaps/download/novideo/{set_id}"),
)
LOOKUPS = (
    ("osu.direct", "https://osu.direct/api/v2/b/{beatmap_id}"),
    ("catboy.best", "https://catboy.best/api/v2/b/{beatmap_id}"),
)
SEARCHES = (
    ("osu.direct", "https://osu.direct/api/v2/search?q={query}&status={status}&mode=0&amount={amount}&offset={offset}"),
    ("catboy.best", "https://catboy.best/api/v2/search?query=&status={status}&mode=0&limit={amount}&offset={offset}"),
)
STATUSES = {"ranked": 1, "approved": 2, "loved": 4}
ARCHIVE_MOST = 200 * 1024 * 1024
REST = 600.0

Fetch = Callable[[str], Awaitable[Optional[bytes]]]

resting: dict[str, float] = {}

def _awake(name: str, clock: Callable[[], float]) -> bool:
    return resting.get(name, 0.0) <= clock()

def _tired(name: str, clock: Callable[[], float]) -> None:
    resting[name] = clock() + REST

def fits(raw: bytes, beatmap_id: Optional[int], checksum: Optional[str]) -> bool:
    if not raw or len(raw) > FILE_MOST or not raw.lstrip(b"\xef\xbb\xbf").startswith(b"osu file format"):
        return False
    if checksum:
        return hashlib.md5(raw).hexdigest() == checksum.lower()
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.startswith("BeatmapID:"):
            return beatmap_id is not None and line.split(":", 1)[1].strip() == str(beatmap_id)
    return False

def pick(archive: bytes, beatmap_id: Optional[int], checksum: Optional[str]) -> Optional[bytes]:
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile:
        return None
    for info in bundle.infolist():
        if not info.filename.lower().endswith(".osu") or info.file_size > FILE_MOST:
            continue
        raw = bundle.read(info)
        if checksum and hashlib.md5(raw).hexdigest() == checksum.lower():
            return raw
        if beatmap_id is not None and not checksum:
            for line in raw.decode("utf-8", errors="replace").splitlines():
                if line.startswith("BeatmapID:"):
                    if line.split(":", 1)[1].strip() == str(beatmap_id):
                        return raw
                    break
    return None

async def _looked_up(fetch: Fetch, beatmap_id: int, clock: Callable[[], float]) -> Optional[dict[str, Any]]:
    for name, address in LOOKUPS:
        if not _awake(name, clock):
            continue
        try:
            raw = await fetch(address.format(beatmap_id=beatmap_id))
            body = json.loads(raw) if raw else None
        except ValueError:
            continue
        except Exception:
            _tired(name, clock)
            continue
        if isinstance(body, dict) and body.get("id") == beatmap_id:
            return body
    return None

async def lookup(fetch: Fetch, beatmap_id: int, clock: Callable[[], float] = time.monotonic) -> Optional[tuple[int, Optional[str]]]:
    body = await _looked_up(fetch, beatmap_id, clock)
    if body is None:
        return None
    set_id = body.get("beatmapset_id")
    checksum = body.get("checksum")
    return (int(set_id) if isinstance(set_id, int) else 0, checksum if isinstance(checksum, str) and checksum else None)

async def describe(fetch: Fetch, beatmap_id: int, clock: Callable[[], float] = time.monotonic) -> Optional[dict[str, Any]]:
    body = await _looked_up(fetch, beatmap_id, clock)
    if body is None:
        return None
    beatmapset = body.get("beatmapset") or body.get("set")
    beatmapset = dict(beatmapset) if isinstance(beatmapset, dict) else {}
    beatmapset["id"] = beatmapset.get("id") or body.get("beatmapset_id")
    entry = _entry(beatmapset, body)
    if entry is not None:
        entry["standard"] = body.get("mode") in ("osu", 0) and not body.get("convert")
    return entry

async def fetch_osu(fetch: Fetch, set_id: int, beatmap_id: Optional[int], checksum: Optional[str],
                    clock: Callable[[], float] = time.monotonic) -> Optional[tuple[str, bytes]]:
    if beatmap_id is not None and (not checksum or not set_id):
        found = await lookup(fetch, beatmap_id, clock)
        if found is not None:
            set_id = set_id or found[0]
            checksum = checksum or found[1]
    if beatmap_id is not None:
        for name, address in FILES:
            if not _awake(name, clock):
                continue
            try:
                raw = await fetch(address.format(beatmap_id=beatmap_id))
            except Exception:
                _tired(name, clock)
                continue
            if raw and fits(raw, beatmap_id, checksum):
                return name, raw
    for name, address in MIRRORS:
        if not _awake(name, clock):
            continue
        try:
            archive = await fetch(address.format(set_id=set_id))
        except Exception:
            _tired(name, clock)
            continue
        if not archive or len(archive) > ARCHIVE_MOST:
            continue
        found = pick(archive, beatmap_id, checksum)
        if found is not None:
            return name, found
    return None

def _entry(beatmapset: dict[str, Any], beatmap: dict[str, Any]) -> Optional[dict[str, Any]]:
    try:
        return {"beatmap_id": int(beatmap["id"]), "beatmapset_id": int(beatmapset["id"]),
                "checksum": beatmap.get("checksum"), "stars": float(beatmap.get("difficulty_rating") or 0.0),
                "status": beatmap.get("status") or beatmapset.get("status"), "mapper": beatmapset.get("creator"),
                "artist": beatmapset.get("artist"), "title": beatmapset.get("title"), "version": beatmap.get("version"),
                "length": int(beatmap.get("total_length") or 0), "max_combo": beatmap.get("max_combo"), "ranked_date": beatmapset.get("ranked_date"),
                "ar": beatmap.get("ar"), "cs": beatmap.get("cs"), "tags": str(beatmapset.get("tags") or "")}
    except (KeyError, TypeError, ValueError):
        return None

def read_search(body: bytes, least: float, most: float) -> list[dict[str, Any]]:
    try:
        sets = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return []
    if isinstance(sets, dict):
        sets = sets.get("beatmapsets") or []
    found = []
    for beatmapset in sets if isinstance(sets, list) else []:
        if not isinstance(beatmapset, dict):
            continue
        for beatmap in beatmapset.get("beatmaps") or []:
            if not isinstance(beatmap, dict) or beatmap.get("mode") not in ("osu", 0) or beatmap.get("convert"):
                continue
            entry = _entry(beatmapset, beatmap)
            if entry is not None and least <= entry["stars"] < most and entry["status"] in STATUSES:
                found.append(entry)
    return found

async def search(fetch: Fetch, status: str, offset: int, amount: int, least: float, most: float,
                 clock: Callable[[], float] = time.monotonic, text: str = "") -> Optional[tuple[str, list[dict[str, Any]], int]]:
    empty = None
    query = quote(f"{text} [beatmaps.difficulty_rating >= {least:g} AND beatmaps.difficulty_rating < {most:g}]".strip(), safe="")
    for name, address in SEARCHES:
        if not _awake(name, clock) or (text and "{query}" not in address):
            continue
        try:
            body = await fetch(address.format(status=STATUSES[status], offset=offset, amount=amount, query=query))
        except Exception:
            _tired(name, clock)
            continue
        if body is None:
            continue
        try:
            decoded = json.loads(body)
            sets = decoded.get("beatmapsets") if isinstance(decoded, dict) else decoded
            if not isinstance(sets, list):
                continue
            seen = len(sets)
        except (ValueError, TypeError, UnicodeDecodeError):
            continue
        entries = read_search(body, least, most)
        if entries:
            return name, entries, seen
        if empty is None or seen > empty[2]:
            empty = name, [], seen
    return empty

def aiohttp_fetch(session) -> Fetch:
    async def fetch(address: str) -> Optional[bytes]:
        async with session.get(address, headers={"User-Agent": USER_AGENT}) as response:
            if response.status != 200:
                return None
            body = bytearray()
            async for chunk in response.content.iter_chunked(1 << 16):
                body.extend(chunk)
                if len(body) > ARCHIVE_MOST:
                    return None
            return bytes(body)
    return fetch

def store(folder: str, raw: bytes) -> str:
    digest = hashlib.md5(raw).hexdigest()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{digest}.osu")
    if not os.path.exists(path):
        temporary = path + ".part"
        with open(temporary, "wb") as handle:
            handle.write(raw)
        os.replace(temporary, path)
    return digest
