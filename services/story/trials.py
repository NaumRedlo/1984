import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from services.duels.generator import Candidate
from services.story.chapters import Goal

EASIER_AFTER = 3
EASIER_BY = 0.5
NEAREST = 3
NOT_PLAY = frozenset({"RX", "AP", "AT", "CN", "TP", "AU"})


def moment(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        found = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def _map_of(score: dict[str, Any]) -> Optional[int]:
    beatmap = score.get("beatmap")
    raw = beatmap.get("id") if isinstance(beatmap, dict) else score.get("beatmap_id")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _mods(score: dict[str, Any]) -> frozenset[str]:
    found = set()
    for mod in score.get("mods") or []:
        acronym = mod.get("acronym") if isinstance(mod, dict) else mod
        if isinstance(acronym, str) and acronym:
            found.add(acronym.upper())
    return frozenset(found)


def _when(score: dict[str, Any]) -> Optional[datetime]:
    return moment(score.get("ended_at")) or moment(score.get("created_at"))


def counts(score: dict[str, Any], beatmap_id: int, since: datetime) -> bool:
    when = _when(score)
    return _map_of(score) == beatmap_id and when is not None and when >= since and not (_mods(score) & NOT_PLAY)


def _passed(score: dict[str, Any]) -> bool:
    return score.get("passed", True) is not False and score.get("rank") != "F"


def _accuracy(score: dict[str, Any]) -> Optional[float]:
    raw = score.get("accuracy")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw) * 100 if raw <= 1 else float(raw)


def _full_combo(score: dict[str, Any]) -> bool:
    for key in ("is_perfect_combo", "legacy_perfect", "perfect"):
        if score.get(key) is True:
            return True
    return False


def meets(goal: Goal, score: dict[str, Any], beatmap_id: int, since: datetime) -> Optional[set[str]]:
    if not counts(score, beatmap_id, since) or not _passed(score):
        return None
    if not goal.mods <= _mods(score):
        return None
    accuracy = _accuracy(score)
    if goal.accuracy is not None and (accuracy is None or accuracy + 1e-9 < goal.accuracy):
        return None
    full = _full_combo(score)
    if goal.full_combo and not full:
        return None
    outcomes = {"passed"}
    if full:
        outcomes.add("fc")
    if score.get("rank") in ("X", "XH"):
        outcomes.add("ss")
    return outcomes


def best(goal: Goal, scores: Iterable[dict[str, Any]], beatmap_id: int, since: datetime) -> tuple[Optional[set[str]], int]:
    tried = 0
    found: Optional[set[str]] = None
    for score in scores:
        if not isinstance(score, dict) or not counts(score, beatmap_id, since):
            continue
        tried += 1
        outcomes = meets(goal, score, beatmap_id, since)
        if outcomes is not None and (found is None or len(outcomes) > len(found)):
            found = outcomes
    return found, tried


def aim(level: float, below: float, eased: bool) -> float:
    return max(0.0, level - below - (EASIER_BY if eased else 0.0))


def pick(candidates: Iterable[Candidate], slot: str, target: float, seed: str, avoid: Iterable[int] = (),
         under: Optional[float] = None) -> Optional[Candidate]:
    skipped = set(avoid)
    fitting = sorted((one for one in candidates if one.slot == slot and one.beatmap_id not in skipped
                      and (under is None or one.stars < under)),
                     key=lambda one: (abs(one.stars - target), one.beatmap_id))
    near = fitting[:NEAREST]
    if not near:
        return None
    digest = hashlib.sha256(f"{seed}:{slot}:{target:.2f}".encode()).digest()
    return near[digest[0] % len(near)]
