"""What changed for a player since they last asked: the update command's arithmetic."""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from db.models.track_snapshot import TrackSnapshot


@dataclass
class Standing:
    pp: Optional[float]
    global_rank: Optional[int]
    country_rank: Optional[int]
    accuracy: Optional[float]
    play_count: Optional[int]
    top: list = field(default_factory=list)  # [(score_id, pp)] best first

    @classmethod
    def of(cls, user_data: dict, best: list) -> "Standing":
        return cls(
            pp=float(user_data.get("pp") or 0),
            global_rank=user_data.get("global_rank"),
            country_rank=user_data.get("country_rank"),
            accuracy=float(user_data.get("accuracy") or 0),
            play_count=int(user_data.get("play_count") or 0),
            top=[(int(s["id"]), float(s.get("pp") or 0)) for s in best if s.get("id")],
        )


@dataclass
class Changes:
    first: bool
    since: Optional[datetime]
    pp: float = 0.0
    global_rank: int = 0  # positive: climbed
    country_rank: int = 0
    accuracy: float = 0.0
    play_count: int = 0
    new_scores: list = field(default_factory=list)  # [(position 1-based, raw score)]

    @property
    def nothing(self) -> bool:
        return not self.first and not self.new_scores and not any(
            (round(self.pp, 2), self.global_rank, self.country_rank, round(self.accuracy, 4), self.play_count))


def compare(before: Optional[Standing], after: Standing, best: list, since: Optional[datetime] = None) -> Changes:
    if before is None:
        return Changes(first=True, since=None)
    known = {sid for sid, _ in before.top}

    def climbed(old, new):
        return (old - new) if old and new else 0

    fresh = [(i + 1, s) for i, s in enumerate(best) if s.get("id") and int(s["id"]) not in known]
    return Changes(
        first=False, since=since,
        pp=(after.pp or 0) - (before.pp or 0),
        global_rank=climbed(before.global_rank, after.global_rank),
        country_rank=climbed(before.country_rank, after.country_rank),
        accuracy=(after.accuracy or 0) - (before.accuracy or 0),
        play_count=(after.play_count or 0) - (before.play_count or 0),
        new_scores=fresh,
    )


async def load(session, osu_user_id: int, ruleset: int) -> Optional[TrackSnapshot]:
    return (await session.execute(select(TrackSnapshot).where(
        TrackSnapshot.osu_user_id == osu_user_id, TrackSnapshot.ruleset == ruleset))).scalar_one_or_none()


def standing_of(row: Optional[TrackSnapshot]) -> Optional[Standing]:
    if row is None:
        return None
    try:
        top = [(int(a), float(b)) for a, b in json.loads(row.top or "[]")]
    except (ValueError, TypeError):
        top = []
    return Standing(row.pp, row.global_rank, row.country_rank, row.accuracy, row.play_count, top)


async def save(session, row: Optional[TrackSnapshot], osu_user_id: int, ruleset: int, now: Standing) -> None:
    if row is None:
        row = TrackSnapshot(osu_user_id=osu_user_id, ruleset=ruleset)
        session.add(row)
    row.taken_at = datetime.now(timezone.utc)
    row.pp, row.global_rank, row.country_rank = now.pp, now.global_rank, now.country_rank
    row.accuracy, row.play_count = now.accuracy, now.play_count
    row.top = json.dumps(now.top)
