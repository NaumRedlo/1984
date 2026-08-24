from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BeatmapRef:
    beatmap_id: int | None       # specific difficulty, if the link named one
    beatmapset_id: int | None    # the set, if known
    mode: str | None             # osu|taiko|fruits|mania from the #<mode>/ anchor


_HOST = r"(?:https?://)?(?:osu|new)\.ppy\.sh"
_SET_DIFF_RE = re.compile(_HOST + r"/beatmapsets/(\d+)#(\w+)/(\d+)", re.I)
_DIFF_RE     = re.compile(_HOST + r"/(?:beatmaps|b)/(\d+)", re.I)
_SET_RE      = re.compile(_HOST + r"/(?:beatmapsets|s)/(\d+)", re.I)

# Cheap pre-filter for the aiogram message filter — matches any beatmap path.
LINK_HINT_RE = re.compile(
    r"(?:osu|new)\.ppy\.sh/(?:beatmapsets|beatmaps|b|s)/\d+", re.I
)


def extract_beatmap_ref(text: str | None) -> BeatmapRef | None:
    if not text:
        return None
    m = _SET_DIFF_RE.search(text)
    if m:
        return BeatmapRef(int(m.group(3)), int(m.group(1)), m.group(2).lower())
    m = _DIFF_RE.search(text)
    if m:
        return BeatmapRef(int(m.group(1)), None, None)
    m = _SET_RE.search(text)
    if m:
        return BeatmapRef(None, int(m.group(1)), None)
    return None


__all__ = ["BeatmapRef", "extract_beatmap_ref", "LINK_HINT_RE"]
