from __future__ import annotations

import re
from dataclasses import dataclass

@dataclass(frozen=True)
class ScoreRef:
    score_id: int
    mode: str | None

_HOST = r"(?:https?://)?(?:osu|new)\.ppy\.sh"
_LEGACY_RE = re.compile(_HOST + r"/scores/(osu|taiko|fruits|mania)/(\d+)", re.I)
_MODERN_RE = re.compile(_HOST + r"/scores/(\d+)", re.I)

LINK_HINT_RE = re.compile(
    r"(?:osu|new)\.ppy\.sh/scores/(?:osu/|taiko/|fruits/|mania/)?\d+", re.I
)

def extract_score_ref(text: str | None) -> ScoreRef | None:
    if not text:
        return None
    m = _LEGACY_RE.search(text)
    if m:
        return ScoreRef(int(m.group(2)), m.group(1).lower())
    m = _MODERN_RE.search(text)
    if m:
        return ScoreRef(int(m.group(1)), None)
    return None

__all__ = ["ScoreRef", "extract_score_ref", "LINK_HINT_RE"]
