"""Pull osu! score references out of arbitrary text.

Recognises the URL shapes osu! uses for an individual score, scheme optional:

  https://osu.ppy.sh/scores/123456789            → modern unified score id
  https://osu.ppy.sh/scores/osu/123456789         → legacy per-ruleset id
  https://osu.ppy.sh/scores/taiko/123456789       → (same, other rulesets)

Used by the auto score-card handler to react to score links pasted in chat.
Mirrors utils/osu/beatmap_link.py's structure/style.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreRef:
    score_id: int
    mode: str | None   # osu|taiko|fruits|mania — only set for the legacy /scores/<mode>/<id> form


_HOST = r"(?:https?://)?(?:osu|new)\.ppy\.sh"
# Legacy (mode-scoped) form tried first so it doesn't get swallowed by the
# modern form's plain \d+ match.
_LEGACY_RE = re.compile(_HOST + r"/scores/(osu|taiko|fruits|mania)/(\d+)", re.I)
_MODERN_RE = re.compile(_HOST + r"/scores/(\d+)", re.I)

# Cheap pre-filter for the aiogram message filter — matches any score path.
LINK_HINT_RE = re.compile(
    r"(?:osu|new)\.ppy\.sh/scores/(?:osu/|taiko/|fruits/|mania/)?\d+", re.I
)


def extract_score_ref(text: str | None) -> ScoreRef | None:
    """Return the first score reference found in `text`, or None."""
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
