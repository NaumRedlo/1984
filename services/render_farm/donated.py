import hashlib
import os
import struct
import time
from typing import Optional

from config.settings import DONATED_REPLAYS_DIR, DONATED_REPLAYS_STORAGE_MOST
from utils.logger import get_logger

logger = get_logger("services.render_farm.donated")

INDEX = "index.tsv"

def looks_like_replay(data: bytes) -> bool:
    if len(data) < 7 or data[0] > 3:
        return False
    version = struct.unpack_from("<i", data, 1)[0]
    return 20_000_000 <= version <= 99_999_999 and data[5] in (0x00, 0x0B)

def _used(folder: str) -> int:
    total = 0
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file():
                total += entry.stat().st_size
    return total

def keep(data: bytes, telegram_id: int, folder: Optional[str] = None, most: Optional[int] = None) -> Optional[bool]:
    folder = folder or DONATED_REPLAYS_DIR
    most = DONATED_REPLAYS_STORAGE_MOST if most is None else most
    os.makedirs(folder, exist_ok=True)
    name = hashlib.md5(data).hexdigest()
    path = os.path.join(folder, f"{name}.osr")
    if os.path.exists(path):
        return True
    if _used(folder) + len(data) > most:
        logger.warning("donated replays are full: %s refused", name)
        return None
    partial = f"{path}.part"
    with open(partial, "wb") as out:
        out.write(data)
    os.replace(partial, path)
    with open(os.path.join(folder, INDEX), "a", encoding="utf-8") as index:
        index.write(f"{name}\t{telegram_id}\t{int(time.time())}\n")
    logger.info("a replay of %d bytes was donated by %s: %s", len(data), telegram_id, name)
    return False
