import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select

from db.models.duel import DuelSkillObservation
from services.duels.generator import Skill
from services.duels.rules import SLOTS, STARS_LEAST

PROFILE_VERSION = "portfolio-v1"
PRIOR = Skill(STARS_LEAST + 0.5, 1.5)
UNKNOWN_U = 1.2
FEW_MAPS = 3
FEW_MAPS_U = 0.8
LEAST_U = 0.25
HALF_LIFE_DAYS = 90
MAP_SHARE_MOST = 0.10
CAPPED_FROM = 10
PERCENTILE = 0.60

@dataclass(frozen=True)
class Observation:
    beatmap_id: int
    slot: str
    stars: float
    value: float
    weight: float
    played_at: datetime

def quality(accuracy: float, combo: int, most_combo: int, misses: int, objects: int) -> Optional[float]:
    if most_combo <= 0 or objects <= 0 or not 0 <= accuracy <= 1:
        return None
    combo_ratio = min(1.0, combo / most_combo)
    miss_ratio = min(1.0, misses / objects)
    return 0.65 * accuracy + 0.25 * combo_ratio + 0.10 * (1 - miss_ratio)

def reach(stars: float, score_quality: float) -> float:
    return max(stars - 1.7, min(stars + 0.3, stars + 2 * (score_quality - 0.85)))

def age_weight(source_weight: float, played_at: datetime, now: datetime) -> float:
    days = max(0.0, (now - played_at).total_seconds() / 86400)
    return source_weight * 2 ** (-days / HALF_LIFE_DAYS)

def _percentile(pairs: list[tuple[float, float]], share: float) -> float:
    ordered = sorted(pairs)
    total = sum(weight for _, weight in ordered)
    walked = 0.0
    for value, weight in ordered:
        walked += weight
        if walked >= share * total - 1e-12:
            return value
    return ordered[-1][0]

def _thinned(observations: list[Observation], now: datetime) -> list[tuple[float, float, int]]:
    per_day: dict[tuple[int, str], Observation] = {}
    for item in sorted(observations, key=lambda o: o.played_at):
        per_day[(item.beatmap_id, item.played_at.date().isoformat())] = item
    kept = list(per_day.values())
    maps = {item.beatmap_id for item in kept}
    if len(maps) < CAPPED_FROM:
        latest: dict[int, Observation] = {}
        for item in sorted(kept, key=lambda o: o.played_at):
            latest[item.beatmap_id] = item
        kept = list(latest.values())
    weighted = [(item.value, age_weight(item.weight, item.played_at, now), item.beatmap_id) for item in kept]
    if len(maps) >= CAPPED_FROM:
        total = sum(weight for _, weight, _ in weighted)
        per_map: dict[int, float] = {}
        for _, weight, beatmap in weighted:
            per_map[beatmap] = per_map.get(beatmap, 0.0) + weight
        cap = MAP_SHARE_MOST * total
        weighted = [(value, weight * min(1.0, cap / per_map[beatmap]) if per_map[beatmap] > cap else weight, beatmap)
                    for value, weight, beatmap in weighted]
    return weighted

def estimate(observations: Iterable[Observation], now: datetime) -> Skill:
    weighted = [item for item in _thinned(list(observations), now) if item[1] > 0]
    if not weighted:
        return PRIOR
    total = sum(weight for _, weight, _ in weighted)
    squares = sum(weight * weight for _, weight, _ in weighted)
    effective = total * total / squares if squares else 0.0
    theta = _percentile([(value, weight) for value, weight, _ in weighted], PERCENTILE)
    spread = max(LEAST_U, 1.2 / math.sqrt(effective)) if effective else UNKNOWN_U
    if len({beatmap for _, _, beatmap in weighted}) < FEW_MAPS:
        spread = max(spread, FEW_MAPS_U)
    return Skill(theta, spread)

def profile(observations: list[Observation], now: datetime) -> tuple[dict[str, Skill], dict[str, str]]:
    general = estimate(observations, now) if observations else PRIOR
    skills, marks = {}, {}
    for slot in SLOTS:
        if slot == "TB":
            continue
        own = [item for item in observations if item.slot == slot]
        if own:
            skills[slot] = estimate(own, now)
            marks[slot] = "measured"
        else:
            skills[slot] = Skill(general.theta, max(UNKNOWN_U, general.u))
            marks[slot] = "UNKNOWN_SLOT" if observations else "PRIOR"
    return skills, marks

async def observations_of(session, player_id: int) -> list[Observation]:
    rows = (await session.scalars(select(DuelSkillObservation).where(DuelSkillObservation.player_id == player_id,
                                                                     DuelSkillObservation.verified.is_(True)))).all()
    return [Observation(int(row.beatmap_id), row.slot, row.stars, row.value, row.weight, row.played_at) for row in rows]

async def keep(session, player_id: int, source: str, beatmap_id: int, slot: str, mods: list[str], stars: float, score_quality: float,
               played_at: datetime, duel_id: Optional[str] = None, attempt_id: Optional[int] = None,
               revision_hash: Optional[str] = None) -> Optional[DuelSkillObservation]:
    held = await session.scalar(select(DuelSkillObservation).where(
        DuelSkillObservation.player_id == player_id, DuelSkillObservation.source == source,
        DuelSkillObservation.beatmap_id == beatmap_id, DuelSkillObservation.played_at == played_at))
    if held is not None:
        return held
    row = DuelSkillObservation(player_id=player_id, source=source, duel_id=duel_id, attempt_id=attempt_id, beatmap_id=beatmap_id,
                               revision_hash=revision_hash, slot=slot, mods=mods, stars=stars, quality=score_quality,
                               value=reach(stars, score_quality), weight=1.0, verified=True, played_at=played_at)
    session.add(row)
    return row

def view(skills: dict[str, Skill], marks: dict[str, str]) -> dict[str, Any]:
    return {"version": PROFILE_VERSION,
            "slots": {slot: {"theta": round(skill.theta, 2), "u": round(skill.u, 2), "mark": marks.get(slot)} for slot, skill in skills.items()}}

def _mods_of(said: Optional[str]) -> frozenset[str]:
    return frozenset(part.strip().upper() for part in (said or "").replace("+", ",").split(",") if part.strip())

def fits_slot(slot: str, played: frozenset[str]) -> bool:
    from services.duels.generator import SLOT_MODS

    wanted = frozenset(SLOT_MODS[slot]) - {"NF"}
    return played - {"NF", "CL"} == wanted

async def from_attempts(session, player_id: int) -> int:
    from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
    from db.models.map_attempt import UserMapAttempt
    from services.duels.features import FEATURE_VERSION
    from services.duels.generator import SLOT_MODS

    rows = (await session.execute(
        select(UserMapAttempt, DuelMapAssignment, DuelMapRevision)
        .join(DuelMapAssignment, DuelMapAssignment.beatmap_id == UserMapAttempt.beatmap_id)
        .join(DuelMapRevision, (DuelMapRevision.beatmap_id == DuelMapAssignment.beatmap_id)
              & (DuelMapRevision.hash == DuelMapAssignment.revision_hash))
        .where(UserMapAttempt.player_id == player_id, UserMapAttempt.passed.is_not(False), DuelMapAssignment.slot.in_([slot for slot in SLOTS if slot != "TB"]), DuelMapAssignment.state.in_(["AUTO_ASSIGNED", "HUMAN_APPROVED"]))
    )).all()
    made = 0
    for attempt, assignment, revision in rows:
        if attempt.ruleset not in (None, 0) or attempt.played_at is None or not fits_slot(assignment.slot, _mods_of(attempt.mods)):
            continue
        analysis = await session.scalar(select(DuelMapAnalysis).where(
            DuelMapAnalysis.revision_hash == revision.hash, DuelMapAnalysis.mods == "".join(SLOT_MODS[assignment.slot]),
            DuelMapAnalysis.feature_version == FEATURE_VERSION))
        if analysis is None or analysis.stars is None or attempt.accuracy is None:
            continue
        most = attempt.map_max_combo or revision.max_combo or 0
        objects = attempt.total_objects or revision.object_count or 0
        score_quality = quality(attempt.accuracy / 100 if attempt.accuracy > 1 else attempt.accuracy, attempt.max_combo or 0, most,
                                attempt.count_miss or 0, objects)
        if score_quality is None:
            continue
        if await keep(session, player_id, "submitted", int(attempt.beatmap_id), assignment.slot, sorted(_mods_of(attempt.mods)),
                      float(analysis.stars), score_quality, attempt.played_at, revision_hash=revision.hash) is not None:
            made += 1
    await session.flush()
    return made

async def from_series(session, duel_id: str) -> int:
    from db.models.duel import DuelAttempt, DuelParticipant, DuelPool, DuelPoolEntry, DuelRound, DuelRoundScore
    from db.models.duel_catalog import DuelMapRevision

    rows = (await session.execute(
        select(DuelRoundScore, DuelAttempt, DuelRound)
        .join(DuelAttempt, DuelAttempt.id == DuelRoundScore.attempt_id).join(DuelRound, DuelRound.id == DuelAttempt.round_id)
        .where(DuelRound.duel_id == duel_id, DuelAttempt.validity == "valid", DuelRound.slot != "TB")
    )).all()
    people = {row.osu_user_id: row.player_id for row in (await session.scalars(
        select(DuelParticipant).where(DuelParticipant.duel_id == duel_id))).all()}
    made = 0
    for score, attempt, played in rows:
        entry = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                     .where(DuelPool.duel_id == duel_id, DuelPoolEntry.slot == played.slot))
        revision = await session.scalar(select(DuelMapRevision).where(DuelMapRevision.beatmap_id == entry.beatmap_id,
                                                                      DuelMapRevision.hash == entry.revision_hash))
        stats = score.statistics or {}
        misses = int(stats.get("count_miss", stats.get("miss", 0)) or 0)
        score_quality = quality(score.accuracy if score.accuracy is not None else -1, score.max_combo or 0,
                                (revision.max_combo if revision else 0) or 0, misses, (revision.object_count if revision else 0) or 0)
        stars = (entry.metrics_snapshot or {}).get("stars")
        player = people.get(score.osu_user_id)
        if score_quality is None or stars is None or player is None:
            continue
        await keep(session, player, "duel", int(entry.beatmap_id), played.slot, list(score.mods or []), float(stars), score_quality,
                   attempt.end_at or attempt.start_at, duel_id=duel_id, attempt_id=attempt.id, revision_hash=entry.revision_hash)
        made += 1
    await session.flush()
    return made
