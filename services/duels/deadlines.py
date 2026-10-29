from datetime import datetime
from typing import Awaitable, Callable, Optional

from sqlalchemy import select

from db.models.duel import Duel, DuelDeadline
from services.duels import states
from services.duels.errors import DuelError
from services.duels.service import Step, _participants
from utils.timeutils import utcnow

Handler = Callable[[Step, Duel, DuelDeadline], Awaitable[None]]

async def _expire_invitation(step: Step, duel: Duel, _deadline: DuelDeadline) -> None:
    if duel.state != states.INVITED:
        return
    event = await step.move(duel, states.EXPIRED, "invitation_expired")
    step.notify(event, [person.player_id for person in await _participants(step.session, duel.id)],
                {"kind": "invitation_expired", "duel": duel.id})

async def _series(step: Step, duel: Duel, deadline: DuelDeadline) -> None:
    from services.duels import series

    await series.on_deadline(step, duel, deadline.kind)

async def _expire_pair(step: Step, duel: Duel, _deadline: DuelDeadline) -> None:
    if duel.state != states.MATCH_OFFERED:
        return
    from services.duels import matchmaker

    people = await _participants(step.session, duel.id)
    await step.move(duel, states.CANCELLED, "offer_expired")
    await matchmaker.back_to_queue(step, duel, people)

HANDLERS: dict[str, Handler] = {
    "invitation": _expire_invitation,
    "pair_confirm": _expire_pair,
    **{kind: _series for kind in ("draft", "preparation", "pool_ack", "ready", "reconnect", "absence", "room_join", "evidence",
                                  "range_offer", "bonus", "room_linger", "round_setup", "round_start")},
}

async def due(factory, now: Optional[datetime] = None, limit: int = 50) -> list[int]:
    now = now or utcnow()
    async with factory() as session:
        rows = await session.scalars(
            select(DuelDeadline.id).where(DuelDeadline.processed_at.is_(None), DuelDeadline.expires_at <= now)
            .order_by(DuelDeadline.expires_at, DuelDeadline.id).limit(limit)
        )
        return list(rows.all())

async def process(factory, deadline_id: int, now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            deadline = await session.scalar(
                select(DuelDeadline).where(DuelDeadline.id == deadline_id).with_for_update(skip_locked=True)
            )
            if deadline is None or deadline.processed_at is not None:
                return "done"
            duel = await session.scalar(select(Duel).where(Duel.id == deadline.duel_id).with_for_update())
            if duel is None or (duel.phase or 0) != (deadline.phase or 0):
                outcome = "stale"
            elif deadline.kind not in HANDLERS:
                outcome = "unknown"
            else:
                try:
                    await HANDLERS[deadline.kind](Step(session, None, now), duel, deadline)
                    outcome = "applied"
                except DuelError:
                    outcome = "refused"
            deadline.processed_at = now
            deadline.outcome = outcome
            return outcome

async def sweep(factory, now: Optional[datetime] = None) -> int:
    handled = 0
    for deadline_id in await due(factory, now):
        if await process(factory, deadline_id, now) == "applied":
            handled += 1
    return handled
