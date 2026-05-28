"""Parse user-supplied import targets — plain IDs or osu.ppy.sh URLs — into
a structured ImportTarget. Pure: no IO, no API calls.

Accepted forms:
    "123456"                               -> beatmap_id  (heuristic by magnitude;
                                              see _classify_bare_id)
    "https://osu.ppy.sh/b/123"             -> beatmap_id
    "https://osu.ppy.sh/beatmaps/123"      -> beatmap_id
    "https://osu.ppy.sh/beatmapsets/456"   -> beatmapset_id
    "https://osu.ppy.sh/beatmapsets/456#osu/789" -> beatmap_id (specific diff)
    "https://osu.ppy.sh/beatmapsets/456/789"     -> beatmap_id

Anything else returns TargetKind.UNKNOWN with the raw text so callers can
report it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TargetKind(str, Enum):
    BEATMAP    = "beatmap"
    BEATMAPSET = "beatmapset"
    UNKNOWN    = "unknown"


@dataclass(frozen=True)
class ImportTarget:
    kind:  TargetKind
    id:    Optional[int]   # beatmap_id or beatmapset_id depending on kind
    raw:   str             # original text — for error reporting


# Patterns are tried in order; the first hit wins.
_PATTERNS: list[tuple[re.Pattern, TargetKind]] = [
    # beatmapsets/<id>#osu/<diff_id>      — specific diff in a set
    (re.compile(r"osu\.ppy\.sh/beatmapsets/\d+(?:[#/])(?:osu/)?(\d+)"),
     TargetKind.BEATMAP),

    # beatmapsets/<id>                    — whole set
    (re.compile(r"osu\.ppy\.sh/beatmapsets/(\d+)"),
     TargetKind.BEATMAPSET),

    # beatmaps/<id>                        — single beatmap (new layout)
    (re.compile(r"osu\.ppy\.sh/beatmaps/(\d+)"),
     TargetKind.BEATMAP),

    # /b/<id>                              — single beatmap (short form)
    (re.compile(r"osu\.ppy\.sh/b/(\d+)"),
     TargetKind.BEATMAP),

    # /s/<id>                              — beatmapset (legacy short form)
    (re.compile(r"osu\.ppy\.sh/s/(\d+)"),
     TargetKind.BEATMAPSET),
]


def parse_import_target(text: str) -> ImportTarget:
    """Classify a single token. Whitespace-stripped input expected."""
    t = (text or "").strip()
    if not t:
        return ImportTarget(TargetKind.UNKNOWN, None, text)

    for pattern, kind in _PATTERNS:
        m = pattern.search(t)
        if m:
            try:
                return ImportTarget(kind, int(m.group(1)), text)
            except ValueError:
                continue

    if t.isdigit():
        # Bare digits: default to BEATMAP. Beatmapset IDs are visually
        # indistinguishable from beatmap IDs, but in practice users supply
        # bare IDs from /b/ links. If we're wrong, the resolver gracefully
        # falls back to beatmapset lookup.
        return ImportTarget(TargetKind.BEATMAP, int(t), text)

    return ImportTarget(TargetKind.UNKNOWN, None, text)


def parse_many(text: str) -> list[ImportTarget]:
    """Split on whitespace/commas and classify each chunk."""
    parts = re.split(r"[\s,]+", (text or "").strip())
    return [parse_import_target(p) for p in parts if p]
