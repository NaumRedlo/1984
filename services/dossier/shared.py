import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import SHARED_REPLAY_DIR
from utils.logger import get_logger

logger = get_logger("services.dossier.shared")

def enabled() -> bool:
    return bool(SHARED_REPLAY_DIR)

def how_many() -> int:
    if not enabled():
        return 0
    total = 0
    for _, _, leaves in os.walk(SHARED_REPLAY_DIR):
        total += sum(1 for leaf in leaves if leaf.endswith(".osr"))
    return total

def _digest(path: str) -> str:
    sha = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            sha.update(block)
    return sha.hexdigest()[:16]

def keep(replay_path: str, verdict: Optional[dict[str, Any]] = None) -> Optional[str]:
    if not enabled():
        return None
    try:
        digest = _digest(replay_path)

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        into = os.path.join(SHARED_REPLAY_DIR, day)
        os.makedirs(into, exist_ok=True)

        landed = os.path.join(into, f"{digest}.osr")
        if os.path.exists(landed):

            logger.debug("replay %s already kept", digest)
        else:
            shutil.copy2(replay_path, landed)

        if verdict is not None:
            with open(os.path.join(into, f"{digest}.json"), "w", encoding="utf-8") as out:
                json.dump(verdict, out, ensure_ascii=False, indent=1, default=str)
        logger.info("kept shared replay %s", digest)
        return landed
    except (OSError, ValueError) as exc:
        logger.warning("could not keep a shared replay: %s", exc)
        return None

__all__ = ["enabled", "keep", "how_many"]
