import hashlib
import json
import math
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from services.duels.rules import SLOTS

GENERATOR_VERSION = "pool-v1"
CORRIDOR = 0.4
WIDE_CORRIDOR = 0.7
RECENT_DAYS = 7
PER_SLOT = 100
BEAM = 50
FINALISTS = 20
TEMPERATURE = 0.25
UNCERTAIN_MOST = 2
SAME_MAPPER_MOST = 2
MAPPER_EXTRA = 0.1
SET_EXTRA = 0.2

SLOT_MODS = {
    "NM1": ("NF",), "NM2": ("NF",), "NM3": ("NF",), "NM4": ("NF",),
    "HD2": ("NF", "HD"), "HR2": ("NF", "HR"), "DT1": ("NF", "DT"), "DT2": ("NF", "DT"), "TB": ("NF",),
}

@dataclass(frozen=True)
class Skill:
    theta: float
    u: float

@dataclass(frozen=True)
class Candidate:
    beatmap_id: int
    beatmapset_id: int
    mapper: str
    slot: str
    stars: float
    level: str
    confidence: Optional[float]
    revision: str
    peak_ratio: Optional[float] = None
    played_days: tuple[Optional[float], Optional[float]] = (None, None)
    revealed_days: tuple[Optional[float], Optional[float]] = (None, None)

@dataclass
class Choice:
    candidate: Candidate
    cost: float
    parts: dict[str, float]

@dataclass
class Pool:
    status: str
    level: int = 0
    entries: list[Choice] = field(default_factory=list)
    cost: Optional[float] = None
    manifest_hash: Optional[str] = None
    deficits: list[str] = field(default_factory=list)
    targets: dict[str, float] = field(default_factory=dict)
    needs_consent: bool = False
    offer: dict[str, tuple[float, float]] = field(default_factory=dict)

def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))

def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))

def reach(skill: Skill, stars: float) -> float:
    return sigmoid((skill.theta - stars) / max(0.35, skill.u))

def targets(a: dict[str, Skill], b: dict[str, Skill]) -> dict[str, float]:
    found = {}
    for slot in SLOTS:
        if slot == "TB":
            continue
        low, high = sorted((a[slot].theta, b[slot].theta))
        target = 0.7 * low + 0.3 * high
        if a[slot].u > 0.8 or b[slot].u > 0.8:
            target -= 0.25
        found[slot] = target
    found["TB"] = median(found.values())
    return found

def familiarity(played: Optional[float], revealed: Optional[float]) -> float:
    play = 2 ** (-played / 14) if played is not None else 0.0
    shown = 0.5 * 2 ** (-revealed / 7) if revealed is not None else 0.0
    return max(play, shown)

def _skill(profile: dict[str, Skill], slot: str) -> Skill:
    if slot == "TB":
        values = [profile[name] for name in SLOTS if name != "TB"]
        return Skill(median(skill.theta for skill in values), max(skill.u for skill in values))
    return profile[slot]

def parts(candidate: Candidate, a: dict[str, Skill], b: dict[str, Skill], baseline: float) -> dict[str, float]:
    reach_a = reach(_skill(a, candidate.slot), candidate.stars)
    reach_b = reach(_skill(b, candidate.slot), candidate.stars)
    load = clamp((abs(reach_a - 0.6) + abs(reach_b - 0.6)) / 2)
    asymmetry = clamp(abs((reach_a - reach_b) - baseline) / 2)
    seen_a = familiarity(candidate.played_days[0], candidate.revealed_days[0])
    seen_b = familiarity(candidate.played_days[1], candidate.revealed_days[1])
    uncertainty = 0.0 if candidate.level == "human" else clamp(1 - (candidate.confidence or 0.0))
    peak = clamp(((candidate.peak_ratio or 0.0) - 2) / 3)
    return {"load": load, "asymmetry": asymmetry, "repeat": clamp(max(seen_a, seen_b)), "familiarity": clamp(abs(seen_a - seen_b)),
            "uncertainty": uncertainty, "peak": peak}

def cost(found: dict[str, float]) -> float:
    return (3 * found["load"] + 2 * found["asymmetry"] + 2 * found["repeat"] + found["familiarity"]
            + 2 * found["uncertainty"] + 0.5 * found["peak"])

def _usable(candidate: Candidate) -> bool:
    if candidate.level in ("confident", "human"):
        return True
    return candidate.level == "uncertain" and candidate.slot != "TB"

def _recent(candidate: Candidate) -> bool:
    return any(days is not None and days < RECENT_DAYS for days in candidate.played_days)

def _eligible(candidate: Candidate, target: float, width: float, level: int) -> bool:
    if candidate.level not in ("confident", "uncertain", "human"):
        return False
    if candidate.slot == "TB" and candidate.level == "uncertain":
        return False
    if abs(candidate.stars - target) > width + 1e-9:
        return False
    return level >= 1 or not _recent(candidate)

def _seed_float(seed: str, level: int) -> float:
    digest = hashlib.sha256(f"{seed}:{GENERATOR_VERSION}:{level}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64

def manifest_hash(entries: list[Choice]) -> str:
    body = [{"slot": choice.candidate.slot, "beatmap_id": choice.candidate.beatmap_id, "revision": choice.candidate.revision,
             "mods": list(SLOT_MODS[choice.candidate.slot])} for choice in sorted(entries, key=lambda c: SLOTS.index(c.candidate.slot))]
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

@dataclass
class _Partial:
    chosen: list[Choice]
    total: float
    maps: frozenset[int]
    sets: dict[int, int]
    mappers: dict[str, int]
    uncertain: int

    def extra(self) -> float:
        over_mapper = sum(max(0, count - SAME_MAPPER_MOST) for count in self.mappers.values())
        over_set = sum(max(0, count - 1) for count in self.sets.values())
        return MAPPER_EXTRA * over_mapper + SET_EXTRA * over_set

    def score(self) -> float:
        return self.total / max(1, len(self.chosen)) + self.extra()

def _search(options: dict[str, list[Choice]], level: int) -> list[_Partial]:
    order = sorted(options, key=lambda slot: (len(options[slot]), SLOTS.index(slot)))
    beam = [_Partial([], 0.0, frozenset(), {}, {}, 0)]
    for slot in order:
        grown = []
        for partial in beam:
            for choice in options[slot]:
                candidate = choice.candidate
                if candidate.beatmap_id in partial.maps:
                    continue
                sets = dict(partial.sets)
                sets[candidate.beatmapset_id] = sets.get(candidate.beatmapset_id, 0) + 1
                mappers = dict(partial.mappers)
                mappers[candidate.mapper.lower()] = mappers.get(candidate.mapper.lower(), 0) + 1
                uncertain = partial.uncertain + (candidate.level == "uncertain")
                if uncertain > UNCERTAIN_MOST:
                    continue
                if level < 2 and (sets[candidate.beatmapset_id] > 1 or mappers[candidate.mapper.lower()] > SAME_MAPPER_MOST):
                    continue
                grown.append(_Partial(partial.chosen + [choice], partial.total + choice.cost, partial.maps | {candidate.beatmap_id},
                                      sets, mappers, uncertain))
        grown.sort(key=lambda partial: (partial.score(), [c.candidate.beatmap_id for c in partial.chosen]))
        beam = grown[:BEAM]
        if not beam:
            return []
    return beam

def _pick(finished: list[_Partial], seed: str, level: int) -> _Partial:
    finalists = sorted(finished, key=lambda partial: (partial.score(), sorted(c.candidate.beatmap_id for c in partial.chosen)))[:FINALISTS]
    best = finalists[0].score()
    weights = [math.exp(-(partial.score() - best) / TEMPERATURE) for partial in finalists]
    point = _seed_float(seed, level) * sum(weights)
    for partial, weight in zip(finalists, weights):
        point -= weight
        if point < 0:
            return partial
    return finalists[-1]

def generate(candidates: list[Candidate], a: dict[str, Skill], b: dict[str, Skill], seed: str) -> Pool:
    aims = targets(a, b)
    by_slot = {slot: [c for c in candidates if c.slot == slot and _usable(c)] for slot in SLOTS}
    missing = [slot for slot in SLOTS if not by_slot[slot]]
    if missing:
        return Pool("unavailable", deficits=missing, targets=aims)
    for level in range(4):
        width = WIDE_CORRIDOR if level >= 3 else CORRIDOR
        fits = {slot: [c for c in by_slot[slot] if _eligible(c, aims[slot], width, level)] for slot in SLOTS}
        empty = [slot for slot in SLOTS if not fits[slot]]
        if empty:
            continue
        everyone = [c for slot in SLOTS for c in fits[slot]]
        baseline = median(reach(_skill(a, c.slot), c.stars) - reach(_skill(b, c.slot), c.stars) for c in everyone)
        options = {}
        for slot in SLOTS:
            scored = []
            for candidate in fits[slot]:
                found = parts(candidate, a, b, baseline)
                scored.append(Choice(candidate, cost(found), found))
            scored.sort(key=lambda choice: (choice.cost, choice.candidate.beatmap_id))
            options[slot] = scored[:PER_SLOT]
        finished = _search(options, level)
        if not finished:
            continue
        chosen = _pick(finished, seed, level)
        entries = sorted(chosen.chosen, key=lambda choice: SLOTS.index(choice.candidate.slot))
        wide = any(abs(choice.candidate.stars - aims[choice.candidate.slot]) > CORRIDOR + 1e-9 for choice in entries)
        return Pool("ready", level, entries, chosen.score(), manifest_hash(entries), targets=aims, needs_consent=wide)
    far = [slot for slot in SLOTS if not any(abs(c.stars - aims[slot]) <= WIDE_CORRIDOR + 1e-9 for c in by_slot[slot])]
    if not far:
        crowded = [slot for slot in SLOTS if all(c.level == "uncertain" for c in by_slot[slot])]
        return Pool("unavailable", 3, targets=aims, deficits=crowded or list(SLOTS))
    offer = {}
    for slot in SLOTS:
        nearest = min((c.stars for c in by_slot[slot]), key=lambda stars: abs(stars - aims[slot]))
        offer[slot] = (round(nearest - CORRIDOR, 2), round(nearest + CORRIDOR, 2))
    return Pool("range_offered", 4, targets=aims, offer=offer, deficits=far)
