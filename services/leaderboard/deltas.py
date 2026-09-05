from __future__ import annotations

DELTA_CATEGORIES = ("pp", "accuracy", "play_count", "play_time", "ranked_score", "hits_per_play")

_SIMPLE_FIELDS = {
    "pp": ("player_pp", "player_pp"),
    "accuracy": ("accuracy", "accuracy"),
    "play_count": ("play_count", "play_count"),
    "play_time": ("play_time", "play_time"),
    "ranked_score": ("ranked_score", "ranked_score"),
}

def _num(value) -> float:
    return float(value or 0)

def delta_for(user, anchor, key: str):
    if anchor is None:
        return None

    if key in _SIMPLE_FIELDS:
        user_attr, anchor_attr = _SIMPLE_FIELDS[key]
        return _num(getattr(user, user_attr)) - _num(getattr(anchor, anchor_attr))

    if key == "hits_per_play":
        plays = _num(user.play_count) - _num(anchor.play_count)
        if plays <= 0:
            return None
        hits = _num(user.total_hits) - _num(anchor.total_hits)
        return hits / plays

    raise ValueError(f"category has no delta mode: {key!r}")

def absolute_for(user, key: str) -> float:
    if key in _SIMPLE_FIELDS:
        return _num(getattr(user, _SIMPLE_FIELDS[key][0]))
    if key == "hits_per_play":
        plays = _num(user.play_count)
        return (_num(user.total_hits) / plays) if plays else 0.0
    raise ValueError(f"category has no delta mode: {key!r}")

def compute_deltas(users, anchors: dict, key: str) -> list[dict]:
    rows = []
    for u in users:
        value = delta_for(u, anchors.get(u.id), key)
        if value is None or value <= 0:
            continue
        rows.append({
            "user_id": u.id,
            "user": u,
            "delta": value,
            "absolute": absolute_for(u, key),
        })

    rows.sort(key=lambda r: (r["delta"], r["absolute"]), reverse=True)
    return rows

def movement(current_pos: int, prev_positions: dict | None, key: str):
    if not prev_positions:
        return None
    prev = prev_positions.get(key)
    if not prev:
        return None
    return prev - current_pos
