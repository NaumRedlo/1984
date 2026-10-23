import hashlib
import io
import os
import zipfile
from typing import Awaitable, Callable, Optional

from services.duels.osu_file import FILE_MOST

MIRRORS = (
    ("osu.direct", "https://osu.direct/api/d/{set_id}"),
    ("catboy.best", "https://catboy.best/d/{set_id}"),
)
ARCHIVE_MOST = 200 * 1024 * 1024

Fetch = Callable[[str], Awaitable[Optional[bytes]]]

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

async def fetch_osu(fetch: Fetch, set_id: int, beatmap_id: Optional[int], checksum: Optional[str]) -> Optional[tuple[str, bytes]]:
    for name, address in MIRRORS:
        try:
            archive = await fetch(address.format(set_id=set_id))
        except Exception:
            continue
        if not archive or len(archive) > ARCHIVE_MOST:
            continue
        found = pick(archive, beatmap_id, checksum)
        if found is not None:
            return name, found
    return None

def aiohttp_fetch(session) -> Fetch:
    async def fetch(address: str) -> Optional[bytes]:
        async with session.get(address) as response:
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
