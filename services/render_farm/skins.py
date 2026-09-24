import hashlib
import os
from typing import Any, Optional

from config import settings
from utils.logger import get_logger

logger = get_logger("services.render_farm.skins")

_digests: dict[tuple[str, int, int], str] = {}

def folder() -> str:
    return settings.RENDER_SKINS_DIR

def available() -> list[str]:
    try:
        names = [entry[:-4] for entry in os.listdir(folder()) if entry.lower().endswith(".osk")]
    except OSError:
        return []
    return sorted(names, key=str.casefold)

def _digest(path: str) -> Optional[str]:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, int(stat.st_mtime), stat.st_size)
    known = _digests.get(key)
    if known:
        return known
    made = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            made.update(chunk)
    _digests[key] = made.hexdigest()
    return _digests[key]

def described(name: Optional[str]) -> Optional[dict[str, Any]]:
    if not name or name not in available():
        return None
    path = os.path.join(folder(), f"{name}.osk")
    digest = _digest(path)
    if digest is None:
        return None
    return {"name": name, "hash": digest, "size": os.path.getsize(path), "path": path}

async def chosen_name(telegram_id: int) -> Optional[str]:
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
    return described(await chosen_name(telegram_id))

async def choose(telegram_id: int, name: Optional[str]) -> None:
    from sqlalchemy import update

    from db.database import AsyncSessionFactory
    from db.models import User

    value = name[:64] if name else None
    async with AsyncSessionFactory() as session:
        await session.execute(update(User).where(User.telegram_id == telegram_id).values(render_skin=value))
        await session.commit()
    logger.info("%s renders with %s", telegram_id, value or "the default skin")
