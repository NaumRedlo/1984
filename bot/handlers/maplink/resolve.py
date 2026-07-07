"""BeatmapRef -> map-card-data resolution, shared by the passive link
auto-detect (handlers.py) and the `map` what-if command (whatif.py).

Split out of handlers.py so whatif.py can depend on this without depending
on handlers.py itself — handlers.py now also depends on whatif.py (to build
the interactive what-if card directly on link auto-detect), and that pair
would otherwise be a circular import.
"""

from __future__ import annotations


def _pick_diff(beatmaps: list[dict]) -> dict | None:
    """For a set-only link, show the hardest osu!std difficulty."""
    if not beatmaps:
        return None
    osu = [b for b in beatmaps if (b.get("mode_int") == 0 or b.get("mode") == "osu")]
    pool = osu or beatmaps
    return max(pool, key=lambda b: float(b.get("difficulty_rating") or 0.0))


def _covers_url(bset: dict, set_id) -> str | None:
    covers = (bset or {}).get("covers") or {}
    return (covers.get("cover@2x") or covers.get("cover")
            or (f"https://assets.ppy.sh/beatmaps/{set_id}/covers/cover@2x.jpg"
                if set_id else None))


def _card_from_beatmap(bm: dict) -> dict:
    bset = bm.get("beatmapset") or {}
    set_id = bm.get("beatmapset_id") or bset.get("id")
    bid = bm.get("id")
    return {
        "beatmap_id": bid,
        "beatmapset_id": set_id,
        "title": bset.get("title") or bm.get("title") or "???",
        "artist": bset.get("artist") or "",
        "creator": bset.get("creator") or "",
        "mapper_id": bset.get("user_id") or bm.get("user_id"),
        "version": bm.get("version") or "",
        "star_rating": bm.get("difficulty_rating"),
        "cs": bm.get("cs"), "ar": bm.get("ar"),
        "od": bm.get("accuracy"), "hp_drain": bm.get("drain"),
        "bpm": bm.get("bpm"), "length": bm.get("total_length"),
        "max_combo": bm.get("max_combo"),
        "status": bm.get("status") or bset.get("status"),
        "cover_url": _covers_url(bset, set_id),
        "url": bm.get("url") or (
            f"https://osu.ppy.sh/beatmapsets/{set_id}#osu/{bid}" if set_id
            else f"https://osu.ppy.sh/beatmaps/{bid}"),
    }


def _card_from_set(bs: dict, diff: dict) -> dict:
    set_id = bs.get("id")
    bid = diff.get("id")
    return {
        "beatmap_id": bid,
        "beatmapset_id": set_id,
        "title": bs.get("title") or "???",
        "artist": bs.get("artist") or "",
        "creator": bs.get("creator") or "",
        "mapper_id": bs.get("user_id"),
        "version": diff.get("version") or "",
        "star_rating": diff.get("difficulty_rating"),
        "cs": diff.get("cs"), "ar": diff.get("ar"),
        "od": diff.get("accuracy"), "hp_drain": diff.get("drain"),
        "bpm": diff.get("bpm") or bs.get("bpm"),
        "length": diff.get("total_length"),
        "max_combo": diff.get("max_combo"),
        "status": diff.get("status") or bs.get("status"),
        "cover_url": _covers_url(bs, set_id),
        "url": f"https://osu.ppy.sh/beatmapsets/{set_id}#osu/{bid}",
    }


async def _resolve_card(ref, api) -> dict | None:
    """Turn a BeatmapRef into card data via the osu! API, or None."""
    if ref.beatmap_id:
        bm = await api.get_beatmap(ref.beatmap_id)
        if bm:
            return _card_from_beatmap(bm)
    if ref.beatmapset_id:
        bs = await api.get_beatmapset(ref.beatmapset_id)
        if bs:
            diff = _pick_diff(bs.get("beatmaps") or [])
            if diff:
                return _card_from_set(bs, diff)
    return None
