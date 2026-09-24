from typing import Optional

# osu!'s rulesets by id, with the name the API uses for each and the one players know it by.
RULESETS = {0: "osu", 1: "taiko", 2: "fruits", 3: "mania"}
TITLES = {0: "osu!", 1: "osu!taiko", 2: "osu!catch", 3: "osu!mania"}

_ALIASES = {
    "osu": 0, "o": 0, "std": 0, "standard": 0,
    "taiko": 1, "t": 1, "тайко": 1,
    "catch": 2, "c": 2, "ctb": 2, "fruits": 2, "кетч": 2,
    "mania": 3, "m": 3, "мания": 3,
}

def parse(word: str) -> Optional[int]:
    """The ruleset a word names ("taiko", "m", "-m 3" split into words is not handled), or None."""
    word = (word or "").strip().lower().lstrip("-")
    return _ALIASES.get(word)

def split_args(text: str) -> tuple[Optional[int], str]:
    """Take a ruleset named as the first or last word off an argument line: "mania cookiezi" → (3, "cookiezi")."""
    words = (text or "").split()
    if words and parse(words[0]) is not None:
        return parse(words[0]), " ".join(words[1:])
    if len(words) > 1 and parse(words[-1]) is not None:
        return parse(words[-1]), " ".join(words[:-1])
    return None, (text or "").strip()

def of_score(raw: dict) -> int:
    """The ruleset a score was played in — for a converted map not the map's own mode."""
    rid = raw.get("ruleset_id")
    if isinstance(rid, int) and rid in RULESETS:
        return rid
    mode = raw.get("mode_int")
    if isinstance(mode, int) and mode in RULESETS:
        return mode
    name = raw.get("mode") or (raw.get("beatmap") or {}).get("mode")
    return {v: k for k, v in RULESETS.items()}.get(name, 0)
