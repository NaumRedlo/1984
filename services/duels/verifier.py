import asyncio
import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.models.duel import DuelAttempt, DuelEvidence
from services.duels.rules import PARAMETERS

SOURCE = "osu-api-v2-matches"
PAGE = 100
PAGES_MOST = 50
CHECKS = (5, 15, 30)
CHECK_EVERY = 60

ALLOWED_TB = ({"NF"}, {"NF", "HD"}, {"NF", "HR"}, {"NF", "HD", "HR"})

def _moment(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

def _mods(raw: Any) -> set[str]:
    found = set()
    for mod in raw or ():
        acronym = mod.get("acronym") if isinstance(mod, dict) else mod
        if isinstance(acronym, str) and acronym:
            found.add(acronym.upper())
    return found

def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

@dataclass
class Expected:
    beatmap_id: int
    players: frozenset[int]
    mods: frozenset[str]
    tiebreak: bool
    started_after: datetime

@dataclass
class Verdict:
    state: str
    reasons: list[str] = field(default_factory=list)
    scores: dict[int, int] = field(default_factory=dict)
    mods: dict[int, list[str]] = field(default_factory=dict)
    winner: Optional[int] = None
    details: dict[int, dict[str, Any]] = field(default_factory=dict)

def judge(game: dict[str, Any], expected: Expected) -> Verdict:
    reasons = []
    if game.get("mode_int", 0) != 0 or game.get("mode", "osu") != "osu":
        reasons.append("mode")
    if str(game.get("scoring_type", "")).lower() != "scorev2":
        reasons.append("score_mode")
    if str(game.get("team_type", "")).lower() != "head-to-head":
        reasons.append("team_mode")
    if game.get("beatmap_id") != expected.beatmap_id:
        reasons.append("beatmap")
    started = _moment(game.get("start_time"))
    if started is None or started < expected.started_after:
        reasons.append("started_before_command")
    if reasons:
        return Verdict("invalid", reasons)
    if not game.get("end_time"):
        return Verdict("pending", ["not_finished"])
    scores = [score for score in game.get("scores") or () if isinstance(score, dict)]
    players = {int(score.get("user_id", 0)) for score in scores}
    if players - expected.players:
        return Verdict("invalid", ["stranger"])
    if players != expected.players:
        return Verdict("incomplete", ["missing_player"])
    global_mods = _mods(game.get("mods"))
    verdict = Verdict("valid")
    for score in scores:
        user = int(score["user_id"])
        effective = global_mods | _mods(score.get("mods"))
        effective.discard("V2")
        if "NF" not in effective:
            reasons.append(f"no_fail:{user}")
        elif expected.tiebreak and effective not in ALLOWED_TB:
            reasons.append(f"mods:{user}")
        elif not expected.tiebreak and effective != set(expected.mods):
            reasons.append(f"mods:{user}")
        verdict.scores[user] = int(score.get("score") or 0)
        verdict.mods[user] = sorted(effective)
        verdict.details[user] = {"accuracy": score.get("accuracy"), "max_combo": score.get("max_combo"),
                                 "statistics": score.get("statistics") or {}}
    if reasons:
        return Verdict("invalid", reasons, verdict.scores, verdict.mods)
    first, second = sorted(verdict.scores.items(), key=lambda pair: -pair[1])
    if first[1] == second[1]:
        verdict.state = "tie"
    else:
        verdict.winner = first[0]
    return verdict

def next_check(since_end: float) -> Optional[float]:
    if since_end >= PARAMETERS["evidence_wait_seconds"]:
        return None
    for mark in CHECKS:
        if since_end < mark:
            return float(mark)
    return float(min(PARAMETERS["evidence_wait_seconds"], (since_end // CHECK_EVERY + 1) * CHECK_EVERY))

class Budget:
    def __init__(self, per_minute: int = PARAMETERS["api_duels_requests_per_minute"], clock: Callable[[], float] = time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.sent: deque[float] = deque()
        self.lock = asyncio.Lock()

    async def take(self) -> None:
        async with self.lock:
            while True:
                now = self.clock()
                while self.sent and self.sent[0] <= now - 60:
                    self.sent.popleft()
                if len(self.sent) < self.per_minute:
                    self.sent.append(now)
                    return
                await asyncio.sleep(self.sent[0] + 60 - now)

async def history(osu, match_id: int, after: Optional[int], budget: Budget) -> tuple[list[dict[str, Any]], Optional[int]]:
    events: list[dict[str, Any]] = []
    cursor = after
    for _ in range(PAGES_MOST):
        await budget.take()
        page = await osu.get_match(match_id, after=cursor, limit=PAGE)
        found = [event for event in (page or {}).get("events") or () if isinstance(event, dict)]
        found = [event for event in found if cursor is None or int(event.get("id", 0)) > cursor]
        if not found:
            break
        events.extend(found)
        cursor = max(int(event["id"]) for event in found)
        if len(found) < PAGE:
            break
    return events, cursor

async def keep(session, match_id: int, event: dict[str, Any]) -> DuelEvidence:
    digest = payload_hash(event)
    game = event.get("game") or {}
    held = await session.scalar(select(DuelEvidence).where(
        DuelEvidence.match_id == match_id, DuelEvidence.event_id == event.get("id"), DuelEvidence.payload_hash == digest))
    if held is not None:
        return held
    row = DuelEvidence(match_id=match_id, event_id=event.get("id"), game_id=game.get("id"), payload_hash=digest,
                       payload=event, source_version=SOURCE)
    session.add(row)
    await session.flush()
    return row

async def bind(session, attempt_id: int, game_id: int) -> bool:
    attempt = await session.get(DuelAttempt, attempt_id)
    if attempt is None:
        return False
    if attempt.game_id == game_id:
        return True
    if attempt.game_id is not None:
        return False
    taken = await session.scalar(select(DuelAttempt.id).where(DuelAttempt.game_id == game_id))
    if taken is not None:
        return False
    attempt.game_id = game_id
    try:
        await session.flush()
    except IntegrityError:
        return False
    return True

def games(events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    for event in events:
        game = event.get("game")
        if isinstance(game, dict) and game.get("id") is not None:
            latest[int(game["id"])] = game
    return latest
