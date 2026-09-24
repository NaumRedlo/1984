import asyncio
from typing import Any, Iterable, Optional

import aiohttp

from config import settings
from utils.logger import get_logger

logger = get_logger("utils.osu.assay_service")

TIMEOUT_SECONDS = 15.0

_session: Optional[aiohttp.ClientSession] = None

def enabled() -> bool:
    return bool(settings.ASSAY_URL)

def mods_of(raw_mods: Iterable[Any] | str | None) -> list[dict]:
    if isinstance(raw_mods, str):
        text = "".join(ch for ch in raw_mods.upper() if ch.isalnum())
        return [{"acronym": text[i:i + 2]} for i in range(0, len(text) - 1, 2) if text[i:i + 2] != "NM"]
    mods = []
    for mod in raw_mods or ():
        if isinstance(mod, dict):
            acronym = str(mod.get("acronym") or "").upper()
            if acronym and acronym != "NM":
                mods.append({"acronym": acronym, "settings": dict(mod.get("settings") or {})})
        elif mod:
            mods.append({"acronym": str(mod).upper()})
    return mods

def _client() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        headers = {"Authorization": f"Bearer {settings.ASSAY_TOKEN}"} if settings.ASSAY_TOKEN else {}
        _session = aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        )
    return _session

async def close() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None

async def _post(path: str, body: dict) -> Optional[dict]:
    if not enabled():
        return None
    try:
        async with _client().post(f"{settings.ASSAY_URL}{path}", json=body) as reply:
            answer = await reply.json(content_type=None)
            if reply.status != 200:
                logger.warning("assay %s answered %s: %s", path, reply.status, answer)
                return None
            return answer
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("assay %s is not reachable: %s", path, exc)
        return None

async def health() -> Optional[dict]:
    if not enabled():
        return None
    try:
        async with _client().get(f"{settings.ASSAY_URL}/health") as reply:
            return await reply.json(content_type=None) if reply.status == 200 else None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None

async def beatmap(beatmap_id: int, *, mods, checksum: Optional[str] = None, ruleset: int = 0) -> Optional[dict]:
    if not beatmap_id:
        return None
    body = {"beatmap_id": int(beatmap_id), "checksum": checksum or None, "mods": mods_of(mods),
            "ruleset": int(ruleset)}
    return await _post("/v1/beatmap", body)

async def score(
    beatmap_id: int,
    *,
    mods,
    statistics: dict,
    checksum: Optional[str] = None,
    accuracy: Optional[float] = None,
    max_combo: Optional[int] = None,
    legacy_total_score: Optional[int] = None,
    is_legacy: Optional[bool] = None,
    ruleset: int = 0,
) -> Optional[dict]:
    counted = {str(k): int(v) for k, v in (statistics or {}).items() if isinstance(v, int) and v >= 0}
    if not beatmap_id or not counted:
        return None
    body = {
        "beatmap_id": int(beatmap_id),
        "checksum": checksum or None,
        "mods": mods_of(mods),
        "statistics": counted,
        "accuracy": accuracy,
        "max_combo": max_combo,
        "legacy_total_score": legacy_total_score or None,
        "is_legacy": is_legacy,
        "ruleset": int(ruleset),
    }
    return await _post("/v1/score", body)

async def whatif(
    beatmap_id: int,
    accuracies: Iterable[float],
    *,
    mods,
    checksum: Optional[str] = None,
    misses: int = 0,
) -> Optional[dict]:
    body = {
        "beatmap_id": int(beatmap_id),
        "checksum": checksum or None,
        "mods": mods_of(mods),
        "accuracies": [float(a) for a in accuracies],
        "misses": int(misses),
    }
    return await _post("/v1/whatif", body)

async def strains(beatmap_id: int, *, mods, points: int = 64,
                  checksum: Optional[str] = None, ruleset: int = 0) -> Optional[dict]:
    if not beatmap_id:
        return None
    body = {"beatmap_id": int(beatmap_id), "checksum": checksum or None,
            "mods": mods_of(mods), "points": int(points), "ruleset": int(ruleset)}
    return await _post("/v1/strains", body)
