import asyncio
import hashlib
import json
import os
from typing import Any, Optional

from config import settings
from utils.logger import get_logger

logger = get_logger("services.render_farm.skins")

_digests: dict[tuple[str, int, int], str] = {}

def folder() -> str:
    return settings.RENDER_SKINS_DIR

def available(telegram_id: Optional[int] = None) -> list[str]:
    try:
        names = [entry[:-4] for entry in os.listdir(folder()) if entry.lower().endswith(".osk")]
    except OSError:
        return []
    if telegram_id is not None:
        try:
            names.extend(entry[:-4] for entry in os.listdir(_personal_folder(telegram_id)) if entry.endswith(".osk"))
        except OSError:
            pass
    return sorted(set(names), key=lambda name: display_name(name, telegram_id).casefold())

def _digest(path: str) -> Optional[str]:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, stat.st_mtime_ns, stat.st_size)
    known = _digests.get(key)
    if known:
        return known
    made = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            made.update(chunk)
    _digests[key] = made.hexdigest()
    return _digests[key]

def described(name: Optional[str], telegram_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    if not name or name not in available(telegram_id):
        return None
    path = _path_for(name, telegram_id)
    if path is None:
        return None
    digest = _digest(path)
    if digest is None:
        return None
    return {"name": name, "hash": digest, "size": os.path.getsize(path), "path": path}

async def chosen_name(telegram_id: int) -> Optional[str]:
    try:
        with open(_choice_path(telegram_id), encoding="utf-8") as source:
            saved = json.load(source)
        if isinstance(saved, dict) and "name" in saved:
            name = saved["name"]
            return name if isinstance(name, str) and name in available(telegram_id) else None
    except (OSError, ValueError, TypeError):
        pass

    from sqlalchemy import select

    from db.database import AsyncSessionFactory
    from db.models import User

    async with AsyncSessionFactory() as session:
        found = await session.execute(select(User.render_skin).where(User.telegram_id == telegram_id))
        for (name,) in found.all():
            if name:
                return name
    return None

async def chosen_for(telegram_id: int) -> Optional[dict[str, Any]]:
    return await asyncio.to_thread(described, await chosen_name(telegram_id), telegram_id)

async def choose(telegram_id: int, name: Optional[str]) -> None:
    import tempfile

    value = name[:64] if name else None
    if value and value not in available(telegram_id):
        raise SkinError("invalid")
    root = _personal_folder(telegram_id)
    os.makedirs(root, exist_ok=True)
    fd, part = tempfile.mkstemp(prefix=".selected-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            json.dump({"name": value}, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(part, _choice_path(telegram_id))
    finally:
        if os.path.exists(part):
            os.unlink(part)
    logger.info("%s renders with %s", telegram_id, value or "the default skin")

class SkinError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

def archive_limit() -> int:
    return max(1, min(int(settings.RENDER_SKIN_MOST), 256 * 1024 * 1024))

def _personal_folder(telegram_id: int) -> str:
    if not isinstance(telegram_id, int) or telegram_id <= 0:
        raise SkinError("invalid")
    return os.path.join(folder(), "uploads", str(telegram_id))

def _choice_path(telegram_id: int) -> str:
    return os.path.join(_personal_folder(telegram_id), "selected.json")

def _path_for(name: str, telegram_id: Optional[int]) -> Optional[str]:
    if not name or os.path.basename(name) != name or "\\" in name:
        return None
    if telegram_id is not None and name.startswith(f"u{telegram_id}-"):
        path = os.path.join(_personal_folder(telegram_id), f"{name}.osk")
        if os.path.isfile(path):
            return path
    path = os.path.join(folder(), f"{name}.osk")
    return path if os.path.isfile(path) else None

def display_name(name: str, telegram_id: Optional[int] = None) -> str:
    path = _path_for(name, telegram_id)
    if path and telegram_id is not None and os.path.dirname(path) == _personal_folder(telegram_id):
        try:
            with open(path + ".json", encoding="utf-8") as source:
                value = json.load(source).get("name")
            if isinstance(value, str) and value:
                return value[:80]
        except (OSError, ValueError, AttributeError):
            pass
    return name

def _validate_archive(path: str) -> None:
    import stat
    import zipfile
    from pathlib import PurePosixPath

    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 8192:
                raise SkinError("invalid")
            remaining = 512 * 1024 * 1024
            roots = set()
            marks = {}
            skin_marks = {"hitcircle.png", "hitcircleoverlay.png", "approachcircle.png", "cursor.png", "cursortrail.png", "hit300.png", "hit0.png", "sliderb0.png", "sliderfollowcircle.png", "followpoint.png", "spinner-circle.png", "menu-back.png", "scorebar-bg.png", "default-0.png"}
            for entry in entries:
                name = PurePosixPath(entry.filename)
                if name.is_absolute() or ".." in name.parts or "\\" in entry.filename or any(":" in p for p in name.parts) or stat.S_ISLNK(entry.external_attr >> 16):
                    raise SkinError("invalid")
                if entry.is_dir():
                    continue
                if entry.file_size > remaining:
                    raise SkinError("invalid")
                with archive.open(entry) as source:
                    while True:
                        chunk = source.read(min(1 << 20, remaining + 1))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise SkinError("invalid")
                stem = name.name.lower().replace("@2x", "")
                if stem == "skin.ini":
                    roots.add(name.parent)
                if stem in skin_marks:
                    marks.setdefault(name.parent, set()).add(stem)
            candidates = roots or {root for root, files in marks.items() if len(files) >= 3}
            if len(candidates) != 1:
                raise SkinError("invalid")
    except SkinError:
        raise
    except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
        raise SkinError("invalid") from exc

def store_upload(telegram_id: int, path: str, filename: str) -> str:
    import shutil
    import tempfile

    size = os.path.getsize(path)
    if size <= 0:
        raise SkinError("invalid")
    if size > archive_limit():
        raise SkinError("too_big")
    _validate_archive(path)
    digest = _digest(path)
    if not digest:
        raise SkinError("invalid")
    name = f"u{telegram_id}-{digest[:32]}"
    root = _personal_folder(telegram_id)
    os.makedirs(root, exist_ok=True)
    target = os.path.join(root, f"{name}.osk")
    if os.path.isfile(target):
        return name
    used = sum(os.path.getsize(os.path.join(root, entry)) for entry in os.listdir(root) if entry.endswith(".osk"))
    if used + size > settings.RENDER_SKIN_STORAGE_MOST:
        raise SkinError("storage_full")
    fd, part = tempfile.mkstemp(prefix=".skin-", dir=root)
    try:
        with os.fdopen(fd, "wb") as destination, open(path, "rb") as source:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(part, target)
        label = os.path.splitext(str(filename).replace("\\", "/").rsplit("/", 1)[-1])[0]
        label = "".join(c for c in label if c.isprintable()).strip()[:80] or "Skin"
        with open(target + ".json", "w", encoding="utf-8") as destination:
            json.dump({"name": label}, destination, ensure_ascii=False)
    finally:
        if os.path.exists(part):
            os.unlink(part)
    return name
