from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

WEIGHT_DECAY = 0.95

MAX_DELTA_AGE = timedelta(days=30)

def _get(score: Any, field: str, default=None):
    if isinstance(score, dict):
        return score.get(field, default)
    return getattr(score, field, default)

@dataclass(frozen=True)
class ScoreDelta:
    kind: str
    amount: float = 0.0
    at: Optional[datetime] = None

def _classify_delta(score: Any, now: datetime) -> Optional[ScoreDelta]:
    changed_at = _get(score, "pp_changed_at")
    if changed_at is None:
        return None
    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=timezone.utc)
    if now - changed_at > MAX_DELTA_AGE:
        return None
    previous_pp = _get(score, "previous_pp")
    if previous_pp is None:
        return ScoreDelta(kind="new", at=changed_at)
    pp = _get(score, "pp", 0.0) or 0.0
    return ScoreDelta(kind="changed", amount=pp - previous_pp, at=changed_at)

def build_top_plays_list(best_scores, *, now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    ordered = sorted(best_scores, key=lambda s: _get(s, "pp", 0.0) or 0.0, reverse=True)

    out = []
    for i, s in enumerate(ordered):
        pp = _get(s, "pp", 0.0) or 0.0
        mods = [m for m in (_get(s, "mods", "") or "").split(",") if m]
        legacy = _get(s, "is_legacy")
        if legacy is None:
            legacy = "CL" in mods
        weight = WEIGHT_DECAY ** i
        delta = _classify_delta(s, now)
        out.append({
            "position": i + 1,
            "score_id": _get(s, "score_id"),
            "beatmap_id": _get(s, "beatmap_id", 0),
            "beatmapset_id": _get(s, "beatmapset_id"),
            "artist": _get(s, "artist", "") or "",
            "title": _get(s, "title", "") or "",
            "version": _get(s, "version", "") or "",
            "creator": _get(s, "creator", "") or "",
            "mods": [m for m in mods if m not in ("CL", "NM")],
            "client": "stable" if legacy else "lazer",
            "star_rating": _get(s, "star_rating") or 0.0,
            "eff_sr": _get(s, "eff_sr") or _get(s, "star_rating") or 0.0,
            "accuracy": _get(s, "accuracy", 0.0) or 0.0,
            "max_combo": _get(s, "max_combo", 0) or 0,
            "map_max_combo": _get(s, "map_max_combo", 0) or 0,
            "rank": _get(s, "rank", "F") or "F",
            "is_fc": bool(_get(s, "is_fc", False)),
            "pp": pp,
            "weight_pct": weight * 100,
            "weighted_pp": pp * weight,
            "delta": delta,
        })
    return out

def total_weighted_pp(built_list: list[dict]) -> float:
    return sum(row["weighted_pp"] for row in built_list)
