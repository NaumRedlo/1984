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

from datetime import datetime, timezone
from typing import Optional


# ── URL parsing ──────────────────────────────────────────────────────────────

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


def find_inprogress_game(
    match_payload: dict,
    beatmap_id: int,
    after: Optional[datetime] = None,
) -> Optional[dict]:
    """Return the most-recent *unfinished* game on `beatmap_id`, or None.

    An unfinished game is one the API still reports with ``end_time is None``:
    the map was started but Bancho has not finalised it.  Normally this is a
    transient (the map is genuinely being played); but if it persists well past
    the map's length it is the classic "Waiting for other players…" Bancho
    stall, where a client never finishes loading / never returns to the lobby
    and the match hangs forever.  The round engine uses the returned game's
    ``start_time`` to age it out and trigger an ``!mp abort`` + replay instead of
    sitting on the dead lobby until the forfeit buffer expires.

    ``after`` (UTC) — ignore games that started before this moment, so a stale
    game from an earlier round can't be mistaken for the current one.
    """
    target = int(beatmap_id)
    best: Optional[tuple[datetime, dict]] = None
    for ev in match_payload.get("events") or []:
        game = ev.get("game")
        if not game or game.get("end_time") is not None:
            continue
        if int(game.get("beatmap_id") or 0) != target:
            continue
        start_dt = _parse_iso_dt(game.get("start_time"))
        if after is not None and start_dt is not None and start_dt < after:
            continue
        key = start_dt or datetime.min.replace(tzinfo=timezone.utc)
        if best is None or key >= best[0]:
            best = (key, game)
    return best[1] if best else None


def game_start_time(game: dict) -> Optional[datetime]:
    """Parsed ``start_time`` of a game dict (UTC), or None if absent/unparseable."""
    return _parse_iso_dt(game.get("start_time"))


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


# ScoreV2 per-mod score multipliers for the difficulty-relevant mods a duel
# can see under Freemod.  Used to normalise the higher-score round comparison
# in RANKED duels so a player can't win a round purely by stacking
# score-inflating mods (HR/HD/DT/FL) against a no-mod opponent.  NF is omitted
# on purpose: an NF score is treated as a fail (see `_decide_round`), so it
# never reaches the score comparison.
SCOREV2_MOD_MULT: dict[str, float] = {
    "EZ": 0.50, "HT": 0.30, "DC": 0.30,
    "HR": 1.10, "DT": 1.20, "NC": 1.20, "HD": 1.06, "FL": 1.12, "SO": 0.90,
}


def mod_acronyms(score: dict) -> set[str]:
    """Set of mod acronyms on a multi `scores[]` entry.

    Tolerates both the lazer shape (list of ``{"acronym": ...}``) and the
    legacy list-of-strings / comma string.  No mods are stripped — the duel
    needs to see NF (fail-rule) and the score-multiplier mods.
    """
    mods = score.get("mods") or []
    out: set[str] = set()
    if isinstance(mods, list):
        for m in mods:
            acro = (m.get("acronym") if isinstance(m, dict) else str(m)) or ""
            acro = acro.upper()
            if acro:
                out.add(acro)
    elif isinstance(mods, str):
        out = {p.strip().upper() for p in mods.replace(",", " ").split() if p.strip()}
    return out


def scorev2_multiplier(mods) -> float:
    """Product of the ScoreV2 multipliers for the given mod acronyms (1.0 if none)."""
    m = 1.0
    for mod in mods:
        m *= SCOREV2_MOD_MULT.get(mod, 1.0)
    return m


def extract_score_stats(score: dict) -> dict:
    """Normalize a multi `scores[]` entry into the fields the duel needs.

    Returns:
        {
          "score": int, "accuracy": float (0..100), "combo": int,
          "misses": int, "passed": bool, "mods": list[str],
          "n_300": int, "n_100": int, "n_50": int,
        }

    n_300/n_100/n_50 are persisted on the round so the DUEL ML pipeline and
    the HPS Ω module can consume them after .osr replay parsing provides UR.
    `mods` feeds the NF fail-rule and the ranked score normalisation in
    `round_engine._decide_round`.
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
        "mods": sorted(mod_acronyms(score)),
        "n_300": n_300,
        "n_100": n_100,
        "n_50": n_50,
    }
