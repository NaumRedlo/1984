import secrets
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from db.models.duel import (Duel, DuelCommand, DuelDeadline, DuelEvent, DuelLease, DuelNotification, DuelOperation,
                            DuelParticipant, DuelQueueEntry, DuelRoom)
from db.models.player import Player
from services.duels import states
from services.duels.errors import DuelError
from services.duels.rules import MODES, PARAMETERS, RULES_VERSION
from utils.timeutils import utcnow

CHANNELS = ("app", "telegram")

def _stamp(moment: Optional[datetime]) -> Optional[str]:
    return moment.isoformat() + "Z" if moment is not None else None

class Step:
    def __init__(self, session, actor_id: Optional[int], now: datetime):
        self.session = session
        self.actor_id = actor_id
        self.now = now
        self.contested: set[int] = set()
        self.keep_room = False

    async def emit(self, duel: Duel, kind: str, payload: dict[str, Any]) -> DuelEvent:
        last = await self.session.scalar(select(func.max(DuelEvent.sequence)).where(DuelEvent.duel_id == duel.id))
        event = DuelEvent(duel_id=duel.id, sequence=(last or 0) + 1, type=kind, payload=payload, actor_id=self.actor_id,
                          state_version=duel.state_version, occurred_at=self.now, received_at=self.now)
        self.session.add(event)
        await self.session.flush()
        return event

    async def move(self, duel: Duel, target: str, reason: Optional[str] = None) -> DuelEvent:
        if not states.allowed(duel.state, target):
            raise DuelError("INVALID_STATE", await snapshot(self.session, duel, self.actor_id))
        source = duel.state
        duel.state = target
        duel.state_version += 1
        duel.phase = (duel.phase or 0) + 1
        duel.updated_at = self.now
        if target in states.TERMINAL:
            duel.finalized_at = self.now
            duel.outcome_reason = reason
            await self.session.execute(delete(DuelLease).where(DuelLease.duel_id == duel.id))
            room = await self.session.scalar(select(DuelRoom.id).where(DuelRoom.duel_id == duel.id, DuelRoom.status == "open"))
            if room is not None and not self.keep_room:
                self.command(duel, "close_room", {"duel": duel.id})
        return await self.emit(duel, "state", {"from": source, "to": target, "reason": reason})

    def touch(self, duel: Duel) -> None:
        duel.state_version += 1
        duel.updated_at = self.now

    def deadline(self, duel: Duel, kind: str, seconds: float) -> None:
        self.session.add(DuelDeadline(duel_id=duel.id, state_version=duel.state_version, phase=duel.phase or 0, kind=kind,
                                      expires_at=self.now + timedelta(seconds=seconds)))

    def command(self, duel: Optional[Duel], kind: str, payload: dict[str, Any]) -> None:
        self.session.add(DuelCommand(duel_id=duel.id if duel is not None else None, kind=kind, payload=payload,
                                     next_attempt_at=self.now, created_at=self.now))

    def notify(self, event: DuelEvent, player_ids: Iterable[int], payload: dict[str, Any]) -> None:
        for player_id in player_ids:
            for channel in CHANNELS:
                self.session.add(DuelNotification(event_id=event.id, player_id=player_id, channel=channel, payload=payload,
                                                  created_at=self.now))

    async def occupy(self, player_ids: Iterable[int], kind: str, duel: Optional[Duel] = None) -> None:
        wanted = sorted(set(player_ids))
        self.contested.update(wanted)
        held = (await self.session.scalars(
            select(DuelLease).where(DuelLease.player_id.in_(wanted)).with_for_update()
        )).all()
        if held:
            raise DuelError("ALREADY_BUSY", await snapshot(self.session, duel, self.actor_id) if duel is not None else None)
        for player_id in wanted:
            self.session.add(DuelLease(player_id=player_id, kind=kind, duel_id=duel.id if duel is not None else None,
                                       since=self.now))
        await self.session.flush()

async def _participants(session, duel_id: str) -> list[DuelParticipant]:
    return list((await session.scalars(
        select(DuelParticipant).where(DuelParticipant.duel_id == duel_id).order_by(DuelParticipant.side)
    )).all())

def _actions(duel: Duel, me: Optional[DuelParticipant]) -> list[str]:
    if me is None or duel.state in states.TERMINAL:
        return []
    if duel.state == states.INVITED:
        return ["accept", "decline"] if me.invited else ["decline"]
    if duel.state == states.MATCH_OFFERED:
        return [] if me.accepted_at is not None else ["accept", "decline"]
    if duel.state in states.BEFORE_PLAY:
        return ["surrender"]
    return []

async def snapshot(session, duel: Duel, viewer_id: Optional[int]) -> dict[str, Any]:
    people = await _participants(session, duel.id)
    me = next((person for person in people if person.player_id == viewer_id), None)
    deadlines = (await session.scalars(
        select(DuelDeadline).where(DuelDeadline.duel_id == duel.id, DuelDeadline.phase == (duel.phase or 0),
                                   DuelDeadline.processed_at.is_(None)).order_by(DuelDeadline.expires_at)
    )).all()
    return {
        "id": duel.id,
        "mode": duel.mode,
        "state": duel.state,
        "version": duel.state_version,
        "rules": duel.rules_version,
        "participants": [{
            "player": person.player_id,
            "osu_user_id": person.osu_user_id,
            "side": person.side,
            "invited": person.invited,
            "accepted": person.accepted_at is not None,
            "connection": person.connection_state,
        } for person in people],
        "wins": {"A": duel.wins_a, "B": duel.wins_b},
        "winner": duel.winner_id,
        "outcome": duel.outcome_reason,
        "deadlines": [{"kind": row.kind, "at": _stamp(row.expires_at)} for row in deadlines],
        "actions": _actions(duel, me),
        "created_at": _stamp(duel.created_at),
        "finalized_at": _stamp(duel.finalized_at),
    }

Body = Callable[[Step], Awaitable[dict[str, Any]]]

async def _run(factory, operation_id: str, actor_id: int, kind: str, body: Body, now: Optional[datetime]) -> dict[str, Any]:
    now = now or utcnow()
    step: Optional[Step] = None
    try:
        async with factory() as session:
            async with session.begin():
                done = await session.get(DuelOperation, operation_id)
                if done is not None:
                    if done.actor_id != actor_id or done.kind != kind:
                        raise DuelError("OPERATION_REUSED")
                    return done.response
                step = Step(session, actor_id, now)
                response = await body(step)
                session.add(DuelOperation(operation_id=operation_id, actor_id=actor_id, kind=kind, duel_id=response.get("id"),
                                          response=response, created_at=now))
            return response
    except IntegrityError:
        pass
    async with factory() as session:
        done = await session.get(DuelOperation, operation_id)
        if done is not None and done.actor_id == actor_id and done.kind == kind:
            return done.response
        if step is not None and step.contested:
            held = await session.scalar(select(func.count()).select_from(DuelLease).where(DuelLease.player_id.in_(step.contested)))
            if held:
                raise DuelError("ALREADY_BUSY")
    raise DuelError("VERSION_CONFLICT")

async def _locked(step: Step, duel_id: str, expected_version: Optional[int]) -> tuple[Duel, DuelParticipant, list[DuelParticipant]]:
    duel = await step.session.scalar(select(Duel).where(Duel.id == duel_id).with_for_update())
    if duel is None:
        raise DuelError("NOT_FOUND")
    people = await _participants(step.session, duel.id)
    me = next((person for person in people if person.player_id == step.actor_id), None)
    if me is None:
        raise DuelError("NOT_PARTICIPANT")
    if expected_version is not None and expected_version != duel.state_version:
        raise DuelError("VERSION_CONFLICT", await snapshot(step.session, duel, step.actor_id))
    return duel, me, people

async def invite(factory, actor_id: int, opponent_id: int, operation_id: str, now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step: Step) -> dict[str, Any]:
        if actor_id == opponent_id:
            raise DuelError("SELF_CHALLENGE")
        players = {row.id: row for row in (await step.session.scalars(
            select(Player).where(Player.id.in_([actor_id, opponent_id]))
        )).all()}
        if len(players) != 2 or any(not row.osu_user_id for row in players.values()):
            raise DuelError("NOT_LINKED")
        pending = await step.session.scalar(
            select(Duel.id).join(DuelParticipant, DuelParticipant.duel_id == Duel.id)
            .where(Duel.state == states.INVITED, DuelParticipant.player_id.in_([actor_id, opponent_id]))
            .group_by(Duel.id).having(func.count(DuelParticipant.id) == 2)
        )
        if pending is not None:
            raise DuelError("ALREADY_INVITED")
        duel = Duel(mode="direct", state=states.INVITED, state_version=1, phase=1, rules_version=RULES_VERSION,
                    seed=secrets.token_hex(32), created_by=actor_id, created_at=step.now, updated_at=step.now)
        step.session.add(duel)
        await step.session.flush()
        first = "A" if secrets.randbelow(2) == 0 else "B"
        second = "B" if first == "A" else "A"
        step.session.add_all([
            DuelParticipant(duel_id=duel.id, player_id=actor_id, osu_user_id=players[actor_id].osu_user_id, side=first,
                            invited=False, accepted_at=step.now),
            DuelParticipant(duel_id=duel.id, player_id=opponent_id, osu_user_id=players[opponent_id].osu_user_id, side=second,
                            invited=True),
        ])
        await step.session.flush()
        event = await step.emit(duel, "invited", {"by": actor_id, "to": opponent_id, "mode": "direct"})
        step.deadline(duel, "invitation", PARAMETERS["invitation_seconds"])
        step.notify(event, [opponent_id], {"kind": "invitation", "duel": duel.id, "by": actor_id})
        await step.session.flush()
        return await snapshot(step.session, duel, actor_id)

    return await _run(factory, operation_id, actor_id, "invite", body, now)

async def accept(factory, duel_id: str, actor_id: int, operation_id: str, expected_version: Optional[int] = None,
                 now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step: Step) -> dict[str, Any]:
        duel, me, people = await _locked(step, duel_id, expected_version)
        if duel.state == states.MATCH_OFFERED and me.accepted_at is None:
            me.accepted_at = step.now
            if any(person.accepted_at is None for person in people):
                step.touch(duel)
                await step.emit(duel, "pair_accepted", {"by": actor_id})
                await step.session.flush()
                return await snapshot(step.session, duel, actor_id)
            await step.move(duel, states.ACCEPTED)
            step.command(duel, "generate_pool", {"duel": duel.id, "version": duel.state_version})
            await step.session.flush()
            return await snapshot(step.session, duel, actor_id)
        if duel.state != states.INVITED or not me.invited:
            raise DuelError("INVALID_STATE", await snapshot(step.session, duel, actor_id))
        await step.occupy([person.player_id for person in people], "duel", duel)
        me.accepted_at = step.now
        event = await step.move(duel, states.ACCEPTED)
        step.command(duel, "generate_pool", {"duel": duel.id, "version": duel.state_version})
        step.notify(event, [person.player_id for person in people if person.player_id != actor_id],
                    {"kind": "accepted", "duel": duel.id, "by": actor_id})
        await step.session.flush()
        return await snapshot(step.session, duel, actor_id)

    return await _run(factory, operation_id, actor_id, "accept", body, now)

async def decline(factory, duel_id: str, actor_id: int, operation_id: str, expected_version: Optional[int] = None,
                  now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step: Step) -> dict[str, Any]:
        duel, me, people = await _locked(step, duel_id, expected_version)
        if duel.state == states.MATCH_OFFERED:
            from services.duels import matchmaker

            await step.move(duel, states.CANCELLED, "offer_declined")
            await matchmaker.back_to_queue(step, duel, people)
            await step.session.flush()
            return await snapshot(step.session, duel, actor_id)
        if duel.state != states.INVITED:
            raise DuelError("INVALID_STATE", await snapshot(step.session, duel, actor_id))
        event = await step.move(duel, states.DECLINED, "declined" if me.invited else "withdrawn")
        step.notify(event, [person.player_id for person in people if person.player_id != actor_id],
                    {"kind": duel.outcome_reason, "duel": duel.id, "by": actor_id})
        await step.session.flush()
        return await snapshot(step.session, duel, actor_id)

    return await _run(factory, operation_id, actor_id, "decline", body, now)

async def surrender(factory, duel_id: str, actor_id: int, operation_id: str, expected_version: Optional[int] = None,
                    now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step: Step) -> dict[str, Any]:
        duel, _, people = await _locked(step, duel_id, expected_version)
        if duel.state not in states.BEFORE_PLAY:
            raise DuelError("INVALID_STATE", await snapshot(step.session, duel, actor_id))
        event = await step.move(duel, states.CANCELLED, "refused_before_start")
        step.notify(event, [person.player_id for person in people if person.player_id != actor_id],
                    {"kind": "refused_before_start", "duel": duel.id, "by": actor_id})
        await step.session.flush()
        return await snapshot(step.session, duel, actor_id)

    return await _run(factory, operation_id, actor_id, "surrender", body, now)

async def cancel(factory, duel_id: str, reason: str, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            duel = await session.scalar(select(Duel).where(Duel.id == duel_id).with_for_update())
            if duel is None or duel.state in states.TERMINAL:
                return None
            step = Step(session, None, now)
            event = await step.move(duel, states.CANCELLED, reason)
            step.notify(event, [person.player_id for person in await _participants(session, duel.id)],
                        {"kind": "cancelled", "duel": duel.id, "reason": reason})
            await session.flush()
            return await snapshot(session, duel, None)

async def enter_queue(factory, actor_id: int, operation_id: str, mode: str = "rated",
                      now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step: Step) -> dict[str, Any]:
        if mode not in MODES or mode == "direct":
            raise DuelError("INVALID_STATE")
        player = await step.session.get(Player, actor_id)
        if player is None or not player.osu_user_id:
            raise DuelError("NOT_LINKED")
        await step.occupy([actor_id], "queue")
        entry = DuelQueueEntry(player_id=actor_id, mode=mode, enqueued_at=step.now, heartbeat_at=step.now, status="waiting")
        step.session.add(entry)
        await step.session.flush()
        return {"queue": {"mode": mode, "since": _stamp(step.now), "status": "waiting"}}

    return await _run(factory, operation_id, actor_id, "enter_queue", body, now)

async def leave_queue(factory, actor_id: int, operation_id: str, now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step: Step) -> dict[str, Any]:
        lease = await step.session.scalar(select(DuelLease).where(DuelLease.player_id == actor_id).with_for_update())
        if lease is None or lease.kind != "queue":
            raise DuelError("INVALID_STATE")
        await step.session.delete(lease)
        entries = (await step.session.scalars(
            select(DuelQueueEntry).where(DuelQueueEntry.player_id == actor_id, DuelQueueEntry.status == "waiting")
        )).all()
        for entry in entries:
            entry.status = "left"
            entry.left_at = step.now
        await step.session.flush()
        return {"queue": None}

    return await _run(factory, operation_id, actor_id, "leave_queue", body, now)

async def read(factory, duel_id: str, viewer_id: int) -> dict[str, Any]:
    async with factory() as session:
        duel = await session.get(Duel, duel_id)
        if duel is None:
            raise DuelError("NOT_FOUND")
        people = await _participants(session, duel.id)
        if all(person.player_id != viewer_id for person in people):
            raise DuelError("NOT_PARTICIPANT")
        return await snapshot(session, duel, viewer_id)

async def mine(factory, viewer_id: int) -> list[dict[str, Any]]:
    async with factory() as session:
        duels = (await session.scalars(
            select(Duel).join(DuelParticipant, DuelParticipant.duel_id == Duel.id)
            .where(DuelParticipant.player_id == viewer_id, Duel.state.notin_(states.TERMINAL))
            .order_by(Duel.created_at)
        )).all()
        return [await snapshot(session, duel, viewer_id) for duel in duels]

async def events_after(factory, duel_id: str, viewer_id: int, sequence: int) -> list[dict[str, Any]]:
    async with factory() as session:
        people = await _participants(session, duel_id)
        if all(person.player_id != viewer_id for person in people):
            raise DuelError("NOT_PARTICIPANT")
        rows = (await session.scalars(
            select(DuelEvent).where(DuelEvent.duel_id == duel_id, DuelEvent.sequence > sequence).order_by(DuelEvent.sequence)
        )).all()
        return [{"sequence": row.sequence, "type": row.type, "payload": row.payload, "version": row.state_version,
                 "at": _stamp(row.occurred_at)} for row in rows]

async def busy(factory, player_ids: Iterable[int]) -> set[int]:
    async with factory() as session:
        rows = await session.scalars(select(DuelLease.player_id).where(DuelLease.player_id.in_(list(player_ids))))
        return set(rows.all())
