import html
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
    (re.compile(r"\bmoscow osu!? event\s+(20\d\d)\b"), r"moe\1"),
    (re.compile(r"\bno ?mod\s*([1-9])\b"), r"nm\1"),
    (re.compile(r"\bhidden\s*([1-9])\b"), r"hd\1"),
    (re.compile(r"\bhard ?rock\s*([1-9])\b"), r"hr\1"),
    (re.compile(r"\bdouble ?time\s*([1-9])\b"), r"dt\1"),
    (re.compile(r"\bfree ?mod\s*([1-9])\b"), r"fm\1"),
    (re.compile(r"\btie ?breaker\b"), "tiebreaker"),
)
LOOSE = {"f", "final", "finals"}
DESCRIBED_MOST = 2048
described_sets: dict[int, str] = {}
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

def _found(text: str, loose: bool, tournaments: set, stages: set, slots: set) -> None:
    text = text.lower()
    for pattern, replacement in PHRASES:
        text = pattern.sub(replacement, text)
    for token in re.split(r"[^a-z0-9!]+", text):
        for piece in _pieces(token.strip("!")):
            if not loose and piece in LOOSE:
                continue
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

def read(said: Optional[str], description: Optional[str] = None) -> dict[str, Any]:
    tournaments, stages, slots = set(), set(), set()
    _found(said or "", True, tournaments, stages, slots)
    _found(plain(description), False, tournaments, stages, slots)
    if not tournaments:
        stages, slots = set(), set()
    return {"raw": said or "", "tournaments": sorted(tournaments), "stages": sorted(stages), "slots": sorted(slots)}

def plain(description: Optional[str]) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", description or ""))

async def description_of(osu, set_id: Optional[int]) -> str:
    if osu is None or not set_id:
        return ""
    if set_id not in described_sets:
        try:
            found = await osu.get_beatmapset(set_id)
        except Exception:
            return ""
        said = (found or {}).get("description")
        if len(described_sets) >= DESCRIBED_MOST:
            described_sets.pop(next(iter(described_sets)))
        described_sets[set_id] = plain(said.get("description") if isinstance(said, dict) else said)
    return described_sets[set_id]

def trimmed(*names: Optional[str]) -> bool:
    return any(TRIMMED.search((name or "").lower()) for name in names)
