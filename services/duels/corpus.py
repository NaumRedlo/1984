import re
from dataclasses import dataclass
from typing import Optional

from services.duels.rules import SLOTS

CORPUS_VERSION = "owc-2021-2025-v1"
_HEADING = re.compile(r"^#{2,6}\s+(.+?)\s*$")
_SLOT = re.compile(r"(?<![A-Z0-9])(NM\d|HD\d|HR\d|DT\d|FM\d|EZ\d|FL\d|TB)(?![A-Z0-9])")
_LINK = re.compile(r"osu\.ppy\.sh/(?:beatmapsets/(\d+)#osu/(\d+)|b/(\d+)|beatmaps/(\d+))")
_STAGES = re.compile(r"(qualifiers|group stage|round of 32|round of 16|quarterfinals|semifinals|finals|grand finals)", re.I)

@dataclass(frozen=True)
class Example:
    year: int
    stage: str
    original_slot: str
    beatmap_id: int
    beatmapset_id: Optional[int]
    source: str

def normalised(slot: str) -> Optional[str]:
    return slot if slot in SLOTS else None

def read_pools(markdown: str, year: int, source: str) -> list[Example]:
    found: list[Example] = []
    stage = "unknown"
    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading:
            named = _STAGES.search(heading.group(1))
            if named:
                stage = named.group(1).lower()
            continue
        slot = _SLOT.search(line)
        link = _LINK.search(line)
        if not slot or not link:
            continue
        set_id = int(link.group(1)) if link.group(1) else None
        beatmap = int(link.group(2) or link.group(3) or link.group(4))
        found.append(Example(year, stage, slot.group(1), beatmap, set_id, source))
    return found

def conflicts(examples: list[Example]) -> set[int]:
    slots: dict[int, set[str]] = {}
    for example in examples:
        slots.setdefault(example.beatmap_id, set()).add(example.original_slot)
    return {beatmap for beatmap, seen in slots.items() if len(seen) > 1}

def distinct(examples: list[Example]) -> list[Example]:
    seen: set[tuple[int, str]] = set()
    kept = []
    for example in sorted(examples, key=lambda e: (e.year, e.stage, e.original_slot, e.beatmap_id)):
        key = (example.beatmap_id, example.original_slot)
        if key in seen:
            continue
        seen.add(key)
        kept.append(example)
    return kept
