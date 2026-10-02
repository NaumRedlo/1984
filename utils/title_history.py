from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, FrozenSet, Iterable, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.models.player import Player
from db.models.witness_session import WitnessSession

SS_RANKS = ("X", "XH")
S_OR_BETTER = ("S", "SH", "X", "XH")
A_OR_BETTER = ("A", "S", "SH", "X", "XH")
RANKED_STATUSES = ("ranked", "approved")
UNRANKED_MODS = frozenset({"RX", "AP", "AT", "CN", "TP"})
SESSION_GAP = timedelta(minutes=30)
SPAN_SLACK = timedelta(minutes=5)

def mod_set(mods) -> FrozenSet[str]:
    if isinstance(mods, (list, tuple, set, frozenset)):
        return frozenset(str(m).strip().upper() for m in mods if m)
    text = str(mods or "").upper()
    if "," in text:
        return frozenset(m.strip() for m in text.split(",") if m.strip())
    return frozenset(text[i:i + 2] for i in range(0, len(text) - 1, 2))

def _is_fc(is_fc, miss, combo, most) -> bool:
    if is_fc is True:
        return True
    if is_fc is False:
        return False
    return miss == 0 and bool(most) and combo is not None and combo >= most

@dataclass(frozen=True)
class Play:
    table: str
    beatmap_id: int
    played_at: Optional[datetime]
    passed: bool
    failed: bool
    rank: str
    score: int
    accuracy: Optional[float]
    max_combo: Optional[int]
    fc: bool
    mods: FrozenSet[str]
    base_sr: float
    sr: float
    bpm: float
    ar: Optional[float]
    length: int
    status: str
    ranked_date: Optional[datetime]

    @property
    def counts(self) -> bool:
        return not (self.mods & UNRANKED_MODS)

    @property
    def ranked(self) -> bool:
        return self.status in RANKED_STATUSES

    @property
    def rate(self) -> float:
        if self.mods & {"DT", "NC"}:
            return 1.5
        if self.mods & {"HT", "DC"}:
            return 0.75
        return 1.0

    @property
    def eff_bpm(self) -> float:
        return self.bpm * self.rate

    @property
    def played_length(self) -> float:
        return self.length / self.rate

    @property
    def style(self) -> FrozenSet[str]:
        return self.mods - {"CL"}

def _play(row, table: str) -> Play:
    timed = table == "attempt"
    passed_flag = getattr(row, "passed", True) if timed else True
    star = float(row.star_rating or 0.0)
    eff = row.eff_sr if row.eff_sr else row.star_rating
    return Play(
        table=table,
        beatmap_id=row.beatmap_id,
        played_at=row.played_at if timed else None,
        passed=passed_flag is True,
        failed=passed_flag is False,
        rank=row.rank or "",
        score=int(row.score or 0),
        accuracy=row.accuracy,
        max_combo=row.max_combo,
        fc=_is_fc(row.is_fc, row.count_miss, row.max_combo, row.map_max_combo),
        mods=mod_set(row.mods),
        base_sr=star,
        sr=float(eff or 0.0),
        bpm=float(row.bpm or 0.0),
        ar=row.ar,
        length=int(row.length or 0),
        status=(row.status or "").lower(),
        ranked_date=row.ranked_date,
    )

@dataclass(frozen=True)
class Span:
    started: datetime
    ended: datetime
    play_seconds: int

def _utc(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None or moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)

def zone_named(name: Optional[str]):
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return timezone.utc

@dataclass
class History:
    attempts: List[Play] = field(default_factory=list)
    undated: List[Play] = field(default_factory=list)
    bests: List[Play] = field(default_factory=list)
    spans: List[Span] = field(default_factory=list)
    zone: object = timezone.utc
    _sessions: Optional[List[List[Play]]] = None

    @property
    def plays(self) -> List[Play]:
        return self.attempts + self.undated + self.bests

    def local(self, moment: datetime) -> datetime:
        return moment.replace(tzinfo=timezone.utc).astimezone(self.zone).replace(tzinfo=None)

    def day(self, moment: datetime) -> date:
        return self.local(moment).date()

    def span_of(self, moment: datetime) -> Optional[int]:
        for slack in (timedelta(0), SPAN_SLACK):
            for at, span in enumerate(self.spans):
                if span.started - slack <= moment <= span.ended + slack:
                    return at
        return None

    def sessions(self) -> List[List[Play]]:
        if self._sessions is not None:
            return self._sessions
        out: List[List[Play]] = []
        current: List[Play] = []
        previous: Optional[datetime] = None
        inside: Optional[int] = None
        for play in self.attempts:
            span = self.span_of(play.played_at)
            if current and (span != inside or (play.played_at - previous) > SESSION_GAP):
                out.append(current)
                current = []
            current.append(play)
            previous, inside = play.played_at, span
        if current:
            out.append(current)
        self._sessions = out
        return out

    def playtime_by_day(self) -> Dict[date, int]:
        inferred: Dict[date, int] = defaultdict(int)
        for session in self.sessions():
            first, last = session[0], session[-1]
            seconds = (last.played_at - first.played_at).total_seconds() + first.played_length
            inferred[self.day(last.played_at)] += int(seconds // 60)
        told: Dict[date, int] = defaultdict(int)
        for span in self.spans:
            told[self.day(span.ended)] += span.play_seconds
        days = set(inferred) | set(told)
        return {day: max(inferred.get(day, 0), told.get(day, 0) // 60) for day in days}

async def load_history(session, player_id: int) -> History:
    cached = session.info.setdefault("title_history", {})
    if player_id in cached:
        return cached[player_id]
    attempts = (await session.execute(
        select(UserMapAttempt).where(UserMapAttempt.player_id == player_id).order_by(UserMapAttempt.played_at, UserMapAttempt.id)
    )).scalars().all()
    bests = (await session.execute(select(UserBestScore).where(UserBestScore.player_id == player_id))).scalars().all()
    spans = (await session.execute(
        select(WitnessSession).where(WitnessSession.player_id == player_id).order_by(WitnessSession.started_at)
    )).scalars().all()
    player = await session.get(Player, player_id)
    history = History(
        attempts=[_play(row, "attempt") for row in attempts if row.played_at is not None],
        undated=[_play(row, "attempt") for row in attempts if row.played_at is None],
        bests=[_play(row, "best") for row in bests],
        spans=[Span(row.started_at, row.ended_at, row.play_seconds or 0) for row in spans],
        zone=zone_named(player.time_zone if player is not None else None),
    )
    cached[player_id] = history
    return history

def forget_history(session, player_id: int) -> None:
    session.info.get("title_history", {}).pop(player_id, None)

def longest_run(plays: Iterable[Play], predicate) -> int:
    best = run = 0
    for play in plays:
        if predicate(play):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best
