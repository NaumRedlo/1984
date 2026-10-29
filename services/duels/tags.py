import re
from typing import Any, Optional

QUERIES = ("owc2026", "owc2025", "owc2024", "owc2023", "owc2022", "owc2021", "the roundtable", "perennial",
           "moe2026", "moe2025", "moe2024", "moe2023")
STAGES = {
    "qualifiers": "qualifiers", "qualifier": "qualifiers", "quals": "qualifiers", "qlf": "qualifiers", "qf": "qf",
    "quarterfinals": "qf", "quarterfinal": "qf", "sf": "sf", "semifinals": "sf", "semifinal": "sf",
    "f": "f", "finals": "f", "final": "f", "gf": "gf", "grandfinals": "gf", "grandfinal": "gf",
}
PHRASES = (
    (re.compile(r"\bround of (\d+)\b"), r"ro\1"),
    (re.compile(r"\bgrand finals?\b"), "grandfinals"),
    (re.compile(r"\bsemi finals?\b"), "semifinals"),
    (re.compile(r"\bquarter finals?\b"), "quarterfinals"),
    (re.compile(r"\bthe roundtable(?:\s+(\d+))?\b"), r"roundtable\1"),
    (re.compile(r"\b(owc|moe)\s+(20\d\d)\b"), r"\1\2"),
    (re.compile(r"\bosu!? world cup\s+(20\d\d)\b"), r"owc\1"),
)
TOURNAMENT = re.compile(r"(owc20\d\d|moe20\d\d|roundtable\d*|trt\d*|perennial\d*)")
PIECE = re.compile(r"((?:nm|hd|hr|dt|fm)[1-9]|qualifiers?|quals|qlf|ro\d+|quarterfinals?|qf|semifinals?|sf|grandfinals?|gf|finals?|tiebreaker|tb|f)")
TRIMMED = re.compile(r"\bcut\s*ver|\bsp(?:ee|e)d\s*up|\btv\s*(?:size|ver)")

def _tournament(word: str) -> str:
    return "roundtable" + word[3:] if word.startswith("trt") else word

def _cover(rest: str) -> Optional[list[str]]:
    found = []
    while rest:
        match = PIECE.match(rest)
        if match is None:
            return None
        found.append(match.group(1))
        rest = rest[match.end():]
    return found

def _pieces(token: str) -> list[str]:
    if TOURNAMENT.fullmatch(token) or PIECE.fullmatch(token):
        return [token]
    head = TOURNAMENT.match(token)
    if head is not None:
        rest = _cover(token[head.end():])
        if rest is not None:
            return [head.group(1), *rest]
    return []

def read(said: Optional[str]) -> dict[str, Any]:
    text = (said or "").lower()
    for pattern, replacement in PHRASES:
        text = pattern.sub(replacement, text)
    tournaments, stages, slots = set(), set(), set()
    for token in re.split(r"[^a-z0-9!]+", text):
        for piece in _pieces(token.strip("!")):
            if TOURNAMENT.fullmatch(piece):
                tournaments.add(_tournament(piece))
            elif piece in ("tb", "tiebreaker"):
                slots.add("TB")
            elif piece[:2] in ("nm", "hd", "hr", "dt", "fm") and piece[2:].isdigit():
                slots.add(piece.upper())
            elif piece.startswith("ro") and piece[2:].isdigit():
                stages.add(piece)
            elif piece in STAGES:
                stages.add(STAGES[piece])
    if not tournaments:
        stages, slots = set(), set()
    return {"raw": said or "", "tournaments": sorted(tournaments), "stages": sorted(stages), "slots": sorted(slots)}

def trimmed(*names: Optional[str]) -> bool:
    return any(TRIMMED.search((name or "").lower()) for name in names)
