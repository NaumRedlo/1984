"""Pure pp-weighting and delta logic for the top-plays card (`tpp`).

Kept free of ORM/DB and rendering concerns so it's testable on plain objects
(SimpleNamespace, dicts) — see tests/unit/test_best_scores.py. Accepts either
`UserBestScore` rows or any object/dict exposing the same field names.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# osu!'s canonical top-100 weighting: rank 1 counts 100%, each next rank
# multiplies by this factor (rank N -> WEIGHT_DECAY**(N-1)).
WEIGHT_DECAY = 0.95

# A pp-delta badge ("+14pp 2 days ago") older than this is simply not shown —
# not flagged as stale, it just quietly stops being interesting.
MAX_DELTA_AGE = timedelta(days=30)


def _get(score: Any, field: str, default=None):
    if isinstance(score, dict):
        return score.get(field, default)
    return getattr(score, field, default)


@dataclass(frozen=True)
class ScoreDelta:
    kind: str          # "new" or "changed"
    amount: float = 0.0    # pp change (only for "changed"); positive = went up
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
    """Sort by pp desc, attach weighted pp / weight % / delta badge per row.

    Returns plain dicts (not ORM objects) so the renderer and tests don't need
    a DB session — mirrors how `top_scores` is built for the profile card.
    """
    now = now or datetime.now(timezone.utc)
    ordered = sorted(best_scores, key=lambda s: _get(s, "pp", 0.0) or 0.0, reverse=True)

    out = []
    for i, s in enumerate(ordered):
        pp = _get(s, "pp", 0.0) or 0.0
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
            "mods": [m for m in (_get(s, "mods", "") or "").split(",") if m],
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
