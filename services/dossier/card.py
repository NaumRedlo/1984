from typing import Optional

_ACRONYM = 2

def _mod_list(joined: str) -> list[str]:
    text = (joined or "").replace(",", "").strip()
    if text.upper() in ("", "NM", "NONE"):
        return []
    return [text[at : at + _ACRONYM] for at in range(0, len(text), _ACRONYM)]

def rank_of(counts: dict, mods: list[str]) -> str:
    great = counts.get("300", 0)
    ok = counts.get("100", 0)
    meh = counts.get("50", 0)
    miss = counts.get("miss", 0)
    total = great + ok + meh + miss
    if total <= 0:
        return "F"

    share_300 = great / total
    share_50 = meh / total
    silver = "HD" in mods or "FL" in mods

    if ok == 0 and meh == 0 and miss == 0:
        return "XH" if silver else "X"
    if share_300 > 0.90 and share_50 < 0.01 and miss == 0:
        return "SH" if silver else "S"
    if (share_300 > 0.80 and miss == 0) or share_300 > 0.90:
        return "A"
    if (share_300 > 0.70 and miss == 0) or share_300 > 0.80:
        return "B"
    if share_300 > 0.60:
        return "C"
    return "D"

def score_from_replay(result: dict, beatmap: Optional[dict]) -> dict:
    counts = result.get("theirs") or {}
    mods = _mod_list(result.get("mods", ""))
    beatmap = beatmap or {}
    return {
        "statistics": {
            "count_300": counts.get("300", 0),
            "count_100": counts.get("100", 0),
            "count_50": counts.get("50", 0),
            "count_miss": counts.get("miss", 0),
        },

        "accuracy": float(result.get("their_accuracy", 0.0)) / 100.0,
        "max_combo": result.get("their_max_combo", 0),
        "mods": mods,
        "rank": rank_of(counts, mods),

        "passed": bool(result.get("finished", True)),
        "beatmap": beatmap,
        "beatmapset": beatmap.get("beatmapset", {}) or {},

        "pp": None,

        "ended_at": "",
    }
