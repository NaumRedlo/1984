import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func, select

from db.models.duel import Duel, DuelGlicko, DuelLease, DuelParticipant, DuelQueueEntry
from db.models.player import Player
from services.duels import glicko, portfolio, states
from services.duels.rating import VERSION
from services.duels.rules import PARAMETERS, RULES_VERSION
from services.duels.service import Step
from utils.timeutils import utcnow

WINDOWS = ((60, 0.40, 0.60, 0.6), (180, 0.35, 0.65, 0.8), (None, 0.30, 0.70, 1.0))
REPEAT_WITHIN = timedelta(minutes=60)
MEETINGS_WITHIN = timedelta(hours=24)
MEETINGS_MOST = 2
UNKNOWN_PENALTY = 0.5
NO_POOL = ("range_declined",)
NO_POOL_REST = timedelta(minutes=10)

@dataclass(frozen=True)
class Seeker:
    entry_id: int
    player_id: int
    enqueued_at: datetime
    rating: float
    theta: Optional[float]

def window(waited: float) -> tuple[float, float, float]:
    for limit, low, high, spread in WINDOWS:
        if limit is None or waited < limit:
            return low, high, spread
    raise AssertionError

def price(first: Seeker, second: Seeker, waited: float, repeat: bool) -> Optional[float]:
    low, high, spread = window(waited)
    p = glicko.closeness(first.rating, second.rating)
    if not low <= p <= high:
        return None
    known = [s.theta for s in (first, second) if s.theta is not None]
    if len(known) == 2:
        distance, extra = abs(known[0] - known[1]), 0.0
    elif len(known) == 1:
        distance, extra = abs(known[0] - portfolio.PRIOR.theta), UNKNOWN_PENALTY
    else:
        distance, extra = 0.0, 0.0
    if distance > spread:
        return None
    return 2 * abs(p - 0.5) + 0.5 * min(distance, 2.0) + 0.2 * (1.0 if repeat else 0.0) + extra

async def _seekers(session, now: datetime, present: Callable[[int], bool]) -> list[Seeker]:
    entries = (await session.scalars(select(DuelQueueEntry).join(DuelLease, DuelLease.player_id == DuelQueueEntry.player_id)
                                     .where(DuelQueueEntry.status == "waiting", DuelQueueEntry.mode == "rated", DuelLease.kind == "queue")
                                     .order_by(DuelQueueEntry.enqueued_at, DuelQueueEntry.id))).all()
    found = []
    for entry in entries:
        if not present(entry.player_id):
            continue
        held = await session.scalar(select(DuelGlicko).where(DuelGlicko.player_id == entry.player_id, DuelGlicko.version == VERSION))
        seen = await portfolio.observations_of(session, entry.player_id)
        theta = portfolio.estimate(seen, now).theta if seen else None
        found.append(Seeker(entry.id, entry.player_id, entry.enqueued_at, held.rating if held else glicko.START_RATING, theta))
    return found

async def _met(session, first: int, second: int, since: datetime) -> tuple[int, Optional[datetime]]:
    rows = (await session.execute(
        select(Duel.created_at).join(DuelParticipant, DuelParticipant.duel_id == Duel.id)
        .where(Duel.mode == "rated", Duel.created_at >= since, Duel.state.notin_([states.CANCELLED, states.EXPIRED, states.DECLINED]),
               DuelParticipant.player_id.in_([first, second]))
        .group_by(Duel.id, Duel.created_at).having(func.count(DuelParticipant.id) == 2)
    )).all()
    return len(rows), max((row[0] for row in rows), default=None)

async def _no_pool(session, first: int, second: int, since: datetime) -> bool:
    found = await session.scalar(
        select(Duel.id).join(DuelParticipant, DuelParticipant.duel_id == Duel.id)
        .where(Duel.mode == "rated", Duel.state == states.CANCELLED, Duel.outcome_reason.in_(NO_POOL), Duel.created_at >= since,
               DuelParticipant.player_id.in_([first, second]))
        .group_by(Duel.id).having(func.count(DuelParticipant.id) == 2).limit(1))
    return found is not None

async def pair(session, now: datetime, present: Callable[[int], bool]) -> Optional[tuple[Seeker, Seeker]]:
    seekers = await _seekers(session, now, present)
    for index, first in enumerate(seekers):
        waited = (now - first.enqueued_at).total_seconds()
        best = None
        for second in seekers[index + 1:] + seekers[:index]:
            if second.player_id == first.player_id:
                continue
            count, last = await _met(session, first.player_id, second.player_id, now - MEETINGS_WITHIN)
            if count >= MEETINGS_MOST or await _no_pool(session, first.player_id, second.player_id, now - NO_POOL_REST):
                continue
            cost = price(first, second, waited, last is not None and now - last < REPEAT_WITHIN)
            if cost is None:
                continue
            key = (cost, second.enqueued_at, second.entry_id)
            if best is None or key < best[0]:
                best = (key, second)
        if best is not None:
            return first, best[1]
    return None

async def offer(factory, present: Callable[[int], bool] = lambda player_id: True, now: Optional[datetime] = None) -> Optional[str]:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            found = await pair(session, now, present)
            if found is None:
                return None
            first, second = found
            entries = (await session.scalars(select(DuelQueueEntry).where(
                DuelQueueEntry.id.in_([first.entry_id, second.entry_id]), DuelQueueEntry.status == "waiting").with_for_update())).all()
            leases = (await session.scalars(select(DuelLease).where(
                DuelLease.player_id.in_([first.player_id, second.player_id]), DuelLease.kind == "queue").with_for_update())).all()
            if len(entries) != 2 or len(leases) != 2:
                return None
            players = {row.id: row for row in (await session.scalars(select(Player).where(
                Player.id.in_([first.player_id, second.player_id])))).all()}
            duel = Duel(mode="rated", state=states.MATCH_OFFERED, state_version=1, phase=1, rules_version=RULES_VERSION,
                        seed=secrets.token_hex(32), created_at=now, updated_at=now)
            session.add(duel)
            await session.flush()
            sides = ("A", "B") if secrets.randbelow(2) == 0 else ("B", "A")
            for seeker, side in zip((first, second), sides):
                session.add(DuelParticipant(duel_id=duel.id, player_id=seeker.player_id, osu_user_id=players[seeker.player_id].osu_user_id,
                                            side=side, invited=True))
            for entry in entries:
                entry.status = "offered"
            for lease in leases:
                lease.kind = "duel"
                lease.duel_id = duel.id
            await session.flush()
            step = Step(session, None, now)
            event = await step.emit(duel, "matched", {"players": [first.player_id, second.player_id]})
            step.deadline(duel, "pair_confirm", PARAMETERS["pair_confirm_seconds"])
            step.notify(event, [first.player_id, second.player_id], {"kind": "matched", "duel": duel.id})
            return duel.id

async def back_to_queue(step: Step, duel: Duel, people: list[DuelParticipant]) -> None:
    for person in people:
        entry = await step.session.scalar(select(DuelQueueEntry).where(DuelQueueEntry.player_id == person.player_id,
                                                                      DuelQueueEntry.status == "offered").with_for_update())
        if entry is None:
            continue
        if person.accepted_at is not None:
            entry.status = "waiting"
            step.session.add(DuelLease(player_id=person.player_id, kind="queue", duel_id=None, since=step.now))
        else:
            entry.status = "left"
            entry.left_at = step.now
    await step.session.flush()
