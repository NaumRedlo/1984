"""DUEL duel match-monitor: extract per-round scores from osu! multi events.

Players link an osu! multiplayer match to their duel; this module finds the
first game on a given beatmap where both players played and returns their
results — including failed passes, which the recent_scores endpoint hides.

API shape (GET /matches/{id}):
    {
      "match": {"id": int, "start_time": str, ...},
      "events": [
        {
          "id": int, "timestamp": str, "detail": {"type": "..."},
          "game": {  # only present for "other"-type events that wrap a game
            "id": int, "start_time": str, "end_time": str | None,
            "beatmap_id": int, "mode": "osu" | ...,
            "scores": [
              {
                "user_id": int, "max_combo": int,
                "total_score": int,        # lazer total (legacy "score" is
                "legacy_total_score": int, # now often 0 — see _score_value)
                "score": int,
                "accuracy": float (0..1),
                "passed": bool,
                "statistics": {"count_300": int, "count_100": int,
                               "count_50": int, "count_miss": int, ...},
                ...
              }, ...
            ]
          } | None
        }, ...
      ],
      "users": [{"id": int, "username": str, ...}, ...],
      "first_event_id": int, "latest_event_id": int
    }

Events are paginated; the v2 endpoint returns the most-recent page by default.
For duel-scope use (single match, recent activity) one fetch is enough — if a
match grows past the page window we re-fetch on next monitor tick.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional


# ── URL parsing ──────────────────────────────────────────────────────────────

_MATCH_URL_PATTERNS = [
    re.compile(r"osu\.ppy\.sh/community/matches/(\d+)", re.IGNORECASE),
    re.compile(r"osu\.ppy\.sh/mp/(\d+)", re.IGNORECASE),
    re.compile(r"\bmp\s*#?\s*(\d+)", re.IGNORECASE),
    re.compile(r"^\s*(\d{4,12})\s*$"),  # bare numeric id
]


def parse_match_url(text: str) -> Optional[int]:
    """Extract an osu! multiplayer match ID from a user-supplied string.

    Accepts:
        https://osu.ppy.sh/community/matches/12345
        https://osu.ppy.sh/mp/12345
        mp #12345  /  mp 12345
        12345    (bare numeric)

    Returns the integer match_id, or None if no recognizable form is found.
    """
    if not text:
        return None
    for pattern in _MATCH_URL_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                value = int(m.group(1))
            except (ValueError, IndexError):
                continue
            if value > 0:
                return value
    return None


# ── Match-event helpers ──────────────────────────────────────────────────────

def _parse_iso_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        # API returns ISO-8601 with trailing Z or +00:00.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _games_in(match_payload: dict) -> list[dict]:
    """Yield game dicts in chronological order (oldest first)."""
    events = match_payload.get("events") or []
    games: list[dict] = []
    for ev in events:
        game = ev.get("game")
        if not game:
            continue
        if game.get("end_time") is None:
            # Game is still running — scores incomplete, skip.
            continue
        games.append(game)
    # Events typically come oldest→newest from the API; sort defensively.
    games.sort(key=lambda g: _parse_iso_dt(g.get("start_time")) or datetime.min.replace(tzinfo=timezone.utc))
    return games


def match_contains_users(match_payload: dict, *user_ids: int) -> bool:
    """True if every user_id is present in the linked multiplayer match.

    The osu! match endpoint exposes lobby participants in the top-level
    ``users`` array before they have submitted any score.  Requiring scores from
    completed games here made it impossible to link a freshly-created room until
    both duel players had already played at least one map.

    For old/partial payloads that do not include ``users`` we keep the previous
    score-based fallback.
    """
    needed = {int(u) for u in user_ids if u}
    if not needed:
        return False

    seen: set[int] = set()

    for user in match_payload.get("users") or []:
        uid = user.get("id")
        if uid is not None:
            seen.add(int(uid))
    if needed.issubset(seen):
        return True

    for game in _games_in(match_payload):
        for score in game.get("scores") or []:
            uid = score.get("user_id")
            if uid is not None:
                seen.add(int(uid))
        if needed.issubset(seen):
            return True
    return needed.issubset(seen)


def find_round_score(
    match_payload: dict,
    beatmap_id: int,
    p1_osu_id: int,
    p2_osu_id: int,
    after: Optional[datetime] = None,
) -> Optional[tuple[dict, dict]]:
    """Find the first completed game on `beatmap_id` where both players played.

    `after` (UTC) — only consider games that started at-or-after this moment.
    Useful so a player can't satisfy a round with a game played before the
    round started.

    Returns `(p1_score_dict, p2_score_dict)` from the API payload, or None.
    The returned dicts are the raw `scores[]` entries — extract accuracy /
    max_combo / count_miss / passed / score from them yourself.
    """
    if not p1_osu_id or not p2_osu_id:
        return None

    p1, p2 = int(p1_osu_id), int(p2_osu_id)

    for game in _games_in(match_payload):
        if int(game.get("beatmap_id") or 0) != int(beatmap_id):
            continue
        start_dt = _parse_iso_dt(game.get("start_time"))
        if after is not None and start_dt is not None and start_dt < after:
            continue

        scores = game.get("scores") or []
        by_uid: dict[int, dict] = {}
        for s in scores:
            uid = s.get("user_id")
            if uid is None:
                continue
            # Take the first score per uid in this game (a player only scores
            # once per game in a multi setup).
            by_uid.setdefault(int(uid), s)

        if p1 in by_uid and p2 in by_uid:
            return by_uid[p1], by_uid[p2]

    return None


def _score_value(score: dict) -> int:
    """Total score for round ranking, tolerant of the osu! API lazer migration.

    On ``/matches`` responses the legacy ``score`` field is now frequently 0;
    the real value lives in ``total_score`` (lazer standardised) with
    ``legacy_total_score`` as a fallback — the same priority the rest of the API
    client uses.  Every score in one game shares a schema, so the resolved key
    is consistent across both players and the round winner is decided correctly.
    """
    val = score.get("total_score")
    if val is None:
        val = score.get("legacy_total_score")
    if val is None:
        val = score.get("score")
    return int(val or 0)


def extract_score_stats(score: dict) -> dict:
    """Normalize a multi `scores[]` entry into the fields the duel needs.

    Returns:
        {
          "score": int, "accuracy": float (0..100), "combo": int,
          "misses": int, "passed": bool,
          "n_300": int, "n_100": int, "n_50": int,
        }

    n_300/n_100/n_50 are persisted on the round so the DUEL ML pipeline and
    the HPS Ω module can consume them after .osr replay parsing provides UR.
    """
    stats = score.get("statistics") or {}
    misses = int(stats.get("count_miss") or stats.get("miss") or 0)
    n_300 = int(stats.get("count_300") or stats.get("great") or 0)
    n_100 = int(stats.get("count_100") or stats.get("ok") or 0)
    n_50 = int(stats.get("count_50") or stats.get("meh") or 0)
    accuracy_raw = score.get("accuracy")
    accuracy = float(accuracy_raw) * 100 if accuracy_raw is not None else 0.0
    return {
        "score": _score_value(score),
        "accuracy": accuracy,
        "combo": int(score.get("max_combo") or 0),
        "misses": misses,
        "passed": bool(score.get("passed")),
        "n_300": n_300,
        "n_100": n_100,
        "n_50": n_50,
    }
