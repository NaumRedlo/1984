"""Resolve a structured ImportTarget to concrete beatmap_ids.

For beatmaps this is identity. For beatmapsets it calls
`api.get_beatmapset()` and returns every osu!standard diff in the set.
"""

from __future__ import annotations

import logging

from services.map_import.parser import ImportTarget, TargetKind

logger = logging.getLogger(__name__)


# osu!standard mode_int. mania=3, taiko=1, ctb=2.
_MODE_STD = 0


async def resolve_target(api_client, target: ImportTarget) -> list[int]:
    """Return all beatmap_ids implied by `target`.

    BEATMAP target → [target.id]
    BEATMAPSET target → every standard difficulty in the set (sorted by SR)
    UNKNOWN → []

    Falls back to a beatmapset lookup if a BEATMAP target turns out to be a
    set ID (the bare-id heuristic in parser can mis-classify).
    """
    if target.kind == TargetKind.UNKNOWN or target.id is None:
        return []

    if target.kind == TargetKind.BEATMAP:
        # Trust the parser — caller can also pass already-validated IDs.
        return [int(target.id)]

    # BEATMAPSET — expand.
    bset = await api_client.get_beatmapset(target.id)
    if not bset:
        # Misclassified bare ID: maybe it's a beatmap_id after all.
        bm = await api_client.get_beatmap(target.id)
        if bm and bm.get("id"):
            return [int(bm["id"])]
        return []

    diffs = bset.get("beatmaps") or []
    ids: list[tuple[float, int]] = []
    for d in diffs:
        if int(d.get("mode_int", _MODE_STD)) != _MODE_STD:
            continue
        bid = d.get("id")
        sr  = float(d.get("difficulty_rating") or 0.0)
        if bid:
            ids.append((sr, int(bid)))
    ids.sort()
    return [bid for _, bid in ids]
