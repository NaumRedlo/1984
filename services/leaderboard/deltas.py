"""Growth-over-the-period maths for the delta leaderboard.

Pure functions over already-loaded rows — no DB access, so they're cheap to test
and the service layer stays in charge of queries.

Most categories are a plain `now − anchor`. Two are not:

* **accuracy** — a lifetime average, so the delta is in *percentage points*
  (a +0.15 move is real progress even though the number is small).
* **hits_per_play** — a ratio, so the delta of the ratio is noise. What's
  meaningful is the ratio *of the period itself*:
  `(hits_now − hits_anchor) / (plays_now − plays_anchor)` — "how many hits per
  play you were landing this week". A player with no plays this period has no
  such ratio at all (division by zero) and simply doesn't rank.
"""

from __future__ import annotations

# Categories that support the delta mode, in leaderboard order.
# (hits_per_play is a period ratio, not a difference — see module docstring.)
DELTA_CATEGORIES = ("pp", "accuracy", "play_count", "play_time", "ranked_score", "hits_per_play")

# Category -> (User attribute, snapshot attribute).
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
    """Growth of `user` on `key` since `anchor`, or None if it can't be ranked.

    None (not 0) means "leave this player out of the standings": no anchor at
    all, or — for hits_per_play — no plays during the period.
    """
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
    """The player's current lifetime value for `key` — shown small under the
    delta ("8 214 всего")."""
    if key in _SIMPLE_FIELDS:
        return _num(getattr(user, _SIMPLE_FIELDS[key][0]))
    if key == "hits_per_play":
        plays = _num(user.play_count)
        return (_num(user.total_hits) / plays) if plays else 0.0
    raise ValueError(f"category has no delta mode: {key!r}")


def compute_deltas(users, anchors: dict, key: str) -> list[dict]:
    """Rank `users` by their growth on `key`.

    `anchors` maps user_id -> snapshot row. Players without a rankable delta are
    dropped (see `delta_for`); so are non-positive ones — a leaderboard of "+0"
    is noise, and the card reports how many sat it out instead.

    Returns rows sorted best-first: {user_id, user, delta, absolute}.
    """
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
    # Ties broken by the lifetime value, so the ordering is stable and sensible.
    rows.sort(key=lambda r: (r["delta"], r["absolute"]), reverse=True)
    return rows


def movement(current_pos: int, prev_positions: dict | None, key: str):
    """How many places the player moved since last period.

    Returns a positive int (climbed), negative (dropped), 0 (held), or None when
    there's no prior standing — the card renders that as `new`, not "▲0".
    """
    if not prev_positions:
        return None
    prev = prev_positions.get(key)
    if not prev:
        return None
    return prev - current_pos
