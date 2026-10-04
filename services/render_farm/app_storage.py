import asyncio
import hashlib
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from config.settings import APP_VIDEO_STORAGE_MOST, APP_VIDEO_TTL_HOURS, APP_VIDEO_USER_STORAGE_MOST, APP_VIDEOS_DIR
from db.models.shared_video import SharedVideo
from utils.timeutils import utcnow


_lock = asyncio.Lock()
_replay_lock = asyncio.Lock()
_REPLAY_KEY = re.compile(r"^[0-9a-f]{32}(?:[0-9a-f]{32})?$")


def path_for(video_id: int) -> Path:
    return Path(APP_VIDEOS_DIR) / f"{int(video_id)}.mp4"


def replay_path(video: SharedVideo, folder: str) -> Path | None:
    key = video.replay_sha256 or video.replay_hash
    return Path(folder) / f"{key}.osr" if key and _REPLAY_KEY.fullmatch(key) else None


async def keep_replay(data: bytes, folder: str, most: int) -> str | None:
    digest = hashlib.sha256(data).hexdigest()
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{digest}.osr"
    async with _replay_lock:
        if target.is_file():
            return digest
        used = sum(path.stat().st_size for path in root.iterdir() if path.is_file())
        if used + len(data) > most:
            return None
        handle, part = tempfile.mkstemp(prefix=".replay-", suffix=".part", dir=root)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(part, target)
        finally:
            if os.path.exists(part):
                os.unlink(part)
    return digest


def install_file(source_path: str, target: Path) -> str:
    digest = hashlib.sha256()
    handle, part = tempfile.mkstemp(prefix=".app-video-", suffix=".part", dir=target.parent)
    try:
        with open(source_path, "rb") as source, os.fdopen(handle, "wb") as out:
            for chunk in iter(lambda: source.read(1 << 20), b""):
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(part, target)
        os.unlink(source_path)
    finally:
        if os.path.exists(part):
            os.unlink(part)
    return digest.hexdigest()


def available(video: SharedVideo, now: datetime | None = None) -> bool:
    until = video.stored_until
    if video.kind != "app" or until is None:
        return False
    moment = now or utcnow()
    if until.tzinfo is not None:
        until = until.astimezone(timezone.utc).replace(tzinfo=None)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return until > moment and path_for(video.id).is_file()


async def store(session, video: SharedVideo, source: str, size: int) -> bool:
    target = path_for(video.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    async with _lock:
        used = sum(path.stat().st_size for path in target.parent.glob("*.mp4") if path.is_file() and path != target)
        if used + size > APP_VIDEO_STORAGE_MOST:
            return False
        mine = (await session.execute(
            select(SharedVideo).where(SharedVideo.kind == "app", SharedVideo.owner_player_id == video.owner_player_id,
                                      SharedVideo.id != video.id)
        )).scalars().all()
        owned = sum(int(row.size or 0) for row in mine if available(row))
        if owned + size > APP_VIDEO_USER_STORAGE_MOST:
            return False
        digest = await asyncio.to_thread(install_file, source, target)
        video.storage_hash = digest
        video.stored_until = utcnow().replace(tzinfo=None) + timedelta(hours=APP_VIDEO_TTL_HOURS)
        video.size = size
        try:
            await session.commit()
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return True


async def purge(session, now: datetime | None = None) -> int:
    moment = (now or utcnow()).astimezone(timezone.utc).replace(tzinfo=None)
    removed = 0
    async with _lock:
        expired = (await session.execute(
            select(SharedVideo.id).where(SharedVideo.kind == "app", SharedVideo.stored_until <= moment)
        )).scalars().all()
        for video_id in expired:
            path = path_for(video_id)
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
    return removed
