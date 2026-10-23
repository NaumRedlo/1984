import hashlib
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import func, select

from db.models.duel import (Duel, DuelAttempt, DuelBonus, DuelDraftAction, DuelParticipant, DuelPool, DuelPoolDelivery, DuelPoolEntry,
                            DuelRoom, DuelRound, DuelRoundScore)
from services.duels import states
from services.duels.errors import DuelError
from services.duels.generator import SLOT_MODS, Pool
from services.duels.rules import PARAMETERS, SLOTS
from services.duels.service import Step, _locked, _participants, _run, snapshot
from utils.timeutils import utcnow

BANS = ("A", "B", "A", "B")
PICKS = ("B", "A", "B", "A")
REGULAR = tuple(slot for slot in SLOTS if slot != "TB")

def _side_of(people: list[DuelParticipant], player_id: int) -> Optional[str]:
    return next((person.side for person in people if person.player_id == player_id), None)

def _player_of(people: list[DuelParticipant], side: str) -> DuelParticipant:
    return next(person for person in people if person.side == side)

def _by_osu(people: list[DuelParticipant], osu_user_id: int) -> Optional[DuelParticipant]:
    return next((person for person in people if person.osu_user_id == osu_user_id), None)

async def _pool(session, duel_id: str) -> tuple[Optional[DuelPool], list[DuelPoolEntry]]:
    pool = await session.scalar(select(DuelPool).where(DuelPool.duel_id == duel_id))
    if pool is None:
        return None, []
    entries = (await session.scalars(select(DuelPoolEntry).where(DuelPoolEntry.pool_id == pool.id))).all()
    return pool, sorted(entries, key=lambda entry: SLOTS.index(entry.slot))

async def _turns(session, duel_id: str) -> list[DuelDraftAction]:
    return list((await session.scalars(
        select(DuelDraftAction).where(DuelDraftAction.duel_id == duel_id).order_by(DuelDraftAction.turn_index)
    )).all())

async def _rounds(session, duel_id: str) -> list[DuelRound]:
    return list((await session.scalars(
        select(DuelRound).where(DuelRound.duel_id == duel_id).order_by(DuelRound.ordinal)
    )).all())

async def _attempt(session, round_id: int) -> Optional[DuelAttempt]:
    return await session.scalar(select(DuelAttempt).where(DuelAttempt.round_id == round_id).order_by(DuelAttempt.attempt_no.desc()))

async def _started(session, duel_id: str) -> bool:
    return bool(await session.scalar(
        select(func.count()).select_from(DuelAttempt).join(DuelRound, DuelRound.id == DuelAttempt.round_id)
        .where(DuelRound.duel_id == duel_id, DuelAttempt.start_at.is_not(None))
    ))

def turn_side(index: int) -> tuple[str, str]:
    if index < len(BANS):
        return "ban", BANS[index]
    return "pick", PICKS[(index - len(BANS)) % len(PICKS)]

def legal(entries: list[DuelPoolEntry], kind: str) -> list[str]:
    return [entry.slot for entry in entries if entry.slot != "TB" and entry.banned_by is None and not entry.used]

def automatic_choice(seed: str, turn_index: int, options: list[str]) -> str:
    digest = hashlib.sha256(f"{seed}:{turn_index}".encode()).digest()
    return sorted(options)[int.from_bytes(digest[:8], "big") % len(options)]

async def view(session, duel: Duel, viewer_id: Optional[int]) -> dict[str, Any]:
    found = await snapshot(session, duel, viewer_id)
    people = await _participants(session, duel.id)
    pool, entries = await _pool(session, duel.id)
    shown = False
    if pool is not None and pool.revealed_at is not None and viewer_id is not None:
        shown = await session.scalar(select(DuelPoolDelivery.id).where(DuelPoolDelivery.pool_id == pool.id,
                                                                       DuelPoolDelivery.player_id == viewer_id)) is not None
    found["pool"] = None if not shown else {
        "manifest": pool.manifest_hash,
        "entries": [{"slot": e.slot, "beatmap_id": e.beatmap_id, "mods": e.mods, "banned_by": e.banned_by, "used": e.used,
                     "stars": (e.metrics_snapshot or {}).get("stars"), "review": e.review_state} for e in entries],
    }
    room = await session.scalar(select(DuelRoom).where(DuelRoom.duel_id == duel.id, DuelRoom.status == "open").order_by(DuelRoom.id.desc()))
    mine = viewer_id is not None and any(person.player_id == viewer_id for person in people)
    found["room"] = None if room is None or not mine else {
        "name": room.room_name, "match_id": room.match_id, "password": (room.capabilities or {}).get("password")}
    turns = await _turns(session, duel.id)
    extra = await session.scalar(select(DuelBonus).where(DuelBonus.duel_id == duel.id))
    found["bonus"] = None if extra is None else {"state": extra.state, "consents": extra.consents, "winner": extra.winner_id,
                                                  "scores": extra.scores}
    found["draft"] = [{"turn": t.turn_index, "type": t.type, "slot": t.slot, "by": t.actor_id, "automatic": t.automatic} for t in turns]
    found["rounds"] = [{"ordinal": r.ordinal, "slot": r.slot, "state": r.state, "winner": r.winner_id, "ties": r.tie_count,
                        "picked_by": r.selected_by} for r in await _rounds(session, duel.id)]
    me = _side_of(people, viewer_id) if viewer_id is not None else None
    actions = list(found["actions"])
    if duel.state in (states.DRAFTING, states.SELECTING) and me is not None:
        kind, side = turn_side(len(turns))
        if side == me:
            actions.append(kind)
    if duel.state == states.PREPARING and me is not None and shown:
        delivered = await session.scalar(select(DuelPoolDelivery).where(DuelPoolDelivery.pool_id == pool.id,
                                                                       DuelPoolDelivery.player_id == viewer_id))
        if delivered is not None and delivered.acknowledged_at is None:
            actions.append("pool_ack")
    if duel.state == states.WAITING_READY and me is not None:
        actions.append("ready")
    if extra is not None and extra.state == "offered" and viewer_id is not None and viewer_id not in extra.consents and me:
        actions.append("bonus")
    if duel.state not in states.TERMINAL and duel.state not in states.UNCOMMITTED and duel.state not in states.BEFORE_PLAY and me:
        actions.append("surrender")
    found["actions"] = actions
    return found

async def _finish(step: Step, duel: Duel, people: list[DuelParticipant], winner: Optional[DuelParticipant], reason: str) -> None:
    duel.winner_id = winner.player_id if winner is not None else None
    rounds = await _rounds(step.session, duel.id)
    offer = reason == "played" and all(r.slot != "TB" for r in rounds)
    step.keep_room = offer
    if duel.state != states.FINALIZING:
        await step.move(duel, states.FINALIZING, reason)
    event = await step.move(duel, states.COMPLETED, reason)
    step.notify(event, [person.player_id for person in people], {"kind": "finished", "duel": duel.id, "winner": duel.winner_id,
                                                               "reason": reason})
    if offer:
        step.session.add(DuelBonus(duel_id=duel.id, state="offered", consents=[], created_at=step.now))
        await step.emit(duel, "bonus_offered", {"seconds": PARAMETERS["bonus_tb_seconds"]})
        step.deadline(duel, "bonus", PARAMETERS["bonus_tb_seconds"])

async def _technical(step: Step, duel: Duel, people: list[DuelParticipant], loser: Optional[DuelParticipant], reason: str) -> None:
    if loser is not None and await _started(step.session, duel.id):
        other = next(person for person in people if person.player_id != loser.player_id)
        await _finish(step, duel, people, other, reason)
        return
    event = await step.move(duel, states.CANCELLED, reason)
    step.notify(event, [person.player_id for person in people], {"kind": "cancelled", "duel": duel.id, "reason": reason})

async def _next_round(step: Step, duel: Duel, people: list[DuelParticipant]) -> None:
    if max(duel.wins_a, duel.wins_b) >= PARAMETERS["series_wins_required"]:
        winner = _player_of(people, "A" if duel.wins_a > duel.wins_b else "B")
        await _finish(step, duel, people, winner, "played")
        return
    if duel.wins_a == duel.wins_b == PARAMETERS["series_wins_required"] - 1:
        await _open_round(step, duel, "TB", None)
        return
    await step.move(duel, states.SELECTING)
    step.deadline(duel, "draft", PARAMETERS["draft_action_seconds"])

async def _open_round(step: Step, duel: Duel, slot: str, picker: Optional[int]) -> DuelRound:
    rounds = await _rounds(step.session, duel.id)
    made = DuelRound(duel_id=duel.id, ordinal=len(rounds) + 1, slot=slot, state="setup", selected_by=picker)
    step.session.add(made)
    await step.session.flush()
    await _new_attempt(step, duel, made)
    return made

async def _new_attempt(step: Step, duel: Duel, made: DuelRound) -> DuelAttempt:
    last = await _attempt(step.session, made.id)
    attempt = DuelAttempt(round_id=made.id, attempt_no=(last.attempt_no + 1) if last else 1, expected_mods=list(SLOT_MODS[made.slot]))
    step.session.add(attempt)
    await step.session.flush()
    if duel.state != states.ROUND_SETUP:
        await step.move(duel, states.ROUND_SETUP)
    step.command(duel, "setup_round", {"duel": duel.id, "round": made.id, "attempt": attempt.id, "slot": made.slot})
    return attempt

async def _act(factory, duel_id: str, actor_id: int, operation_id: str, kind: str, expected_version: Optional[int],
               body: Callable[[Step, Duel, DuelParticipant, list[DuelParticipant]], Awaitable[None]], now: Optional[datetime]):
    async def run(step: Step) -> dict[str, Any]:
        duel, me, people = await _locked(step, duel_id, expected_version)
        await body(step, duel, me, people)
        await step.session.flush()
        return await view(step.session, duel, actor_id)
    return await _run(factory, operation_id, actor_id, kind, run, now)

async def pool_ack(factory, duel_id: str, actor_id: int, manifest: str, operation_id: str, expected_version: Optional[int] = None,
                   now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step, duel, me, people):
        if duel.state != states.PREPARING:
            raise DuelError("INVALID_STATE", await view(step.session, duel, actor_id))
        pool, _ = await _pool(step.session, duel.id)
        if pool is None or pool.manifest_hash != manifest:
            raise DuelError("MAP_REVISION_CHANGED", await view(step.session, duel, actor_id))
        delivery = await step.session.scalar(select(DuelPoolDelivery).where(DuelPoolDelivery.pool_id == pool.id,
                                                                           DuelPoolDelivery.player_id == actor_id))
        if delivery is None or delivery.acknowledged_at is not None:
            raise DuelError("INVALID_STATE", await view(step.session, duel, actor_id))
        delivery.acknowledged_at = step.now
        step.touch(duel)
        await step.emit(duel, "pool_ack", {"player": actor_id})
        waiting = await step.session.scalar(select(func.count()).select_from(DuelPoolDelivery).where(
            DuelPoolDelivery.pool_id == pool.id, DuelPoolDelivery.acknowledged_at.is_(None)))
        if not waiting:
            await step.emit(duel, "preparation", {"seconds": PARAMETERS["preparation_seconds"]})
            step.deadline(duel, "preparation", PARAMETERS["preparation_seconds"])
    return await _act(factory, duel_id, actor_id, operation_id, "pool_ack", expected_version, body, now)

async def draft(factory, duel_id: str, actor_id: int, slot: str, operation_id: str, expected_version: Optional[int] = None,
                now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step, duel, me, people):
        await _take_turn(step, duel, people, me, slot, operation_id, automatic=False)
    return await _act(factory, duel_id, actor_id, operation_id, "draft", expected_version, body, now)

async def _take_turn(step: Step, duel: Duel, people: list[DuelParticipant], actor: DuelParticipant, slot: str,
                     operation_id: Optional[str], automatic: bool) -> None:
    if duel.state not in (states.DRAFTING, states.SELECTING):
        raise DuelError("INVALID_STATE", await view(step.session, duel, actor.player_id))
    turns = await _turns(step.session, duel.id)
    kind, side = turn_side(len(turns))
    if (kind == "ban") != (duel.state == states.DRAFTING) or actor.side != side:
        raise DuelError("NOT_YOUR_TURN", await view(step.session, duel, actor.player_id))
    _, entries = await _pool(step.session, duel.id)
    if slot not in legal(entries, kind):
        raise DuelError("SLOT_UNAVAILABLE", await view(step.session, duel, actor.player_id))
    entry = next(entry for entry in entries if entry.slot == slot)
    step.session.add(DuelDraftAction(duel_id=duel.id, turn_index=len(turns), actor_id=actor.player_id, type=kind, slot=slot,
                                     automatic=automatic, operation_id=operation_id, created_at=step.now))
    actor.missed = (actor.missed or 0) + 1 if automatic else 0
    step.touch(duel)
    await step.emit(duel, kind, {"slot": slot, "by": actor.player_id, "automatic": automatic})
    if kind == "ban":
        entry.banned_by = actor.player_id
        if len(turns) + 1 == len(BANS):
            await step.move(duel, states.SELECTING)
        else:
            duel.phase = (duel.phase or 0) + 1
        step.deadline(duel, "draft", PARAMETERS["draft_action_seconds"])
        return
    entry.used = True
    await _open_round(step, duel, slot, actor.player_id)

async def ready(factory, duel_id: str, actor_id: int, operation_id: str, tb_mods: Optional[list[str]] = None,
                expected_version: Optional[int] = None, now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step, duel, me, people):
        if duel.state != states.WAITING_READY:
            raise DuelError("INVALID_STATE", await view(step.session, duel, actor_id))
        rounds = await _rounds(step.session, duel.id)
        current = rounds[-1]
        if current.slot == "TB":
            chosen = sorted(set(tb_mods or []) | {"NF"})
            if set(chosen) not in ({"NF"}, {"NF", "HD"}, {"NF", "HR"}, {"NF", "HD", "HR"}):
                raise DuelError("INVALID_STATE", await view(step.session, duel, actor_id))
            picked = dict(current.tb_mods or {})
            picked[str(actor_id)] = chosen
            current.tb_mods = picked
        me.connection_state = "ready"
        step.touch(duel)
        await step.emit(duel, "ready", {"player": actor_id})
        if all(person.connection_state == "ready" for person in people):
            for person in people:
                person.connection_state = "playing"
            attempt = await _attempt(step.session, current.id)
            attempt.commanded_at = step.now
            await step.move(duel, states.STARTING)
            step.command(duel, "start_round", {"duel": duel.id, "round": current.id})
    return await _act(factory, duel_id, actor_id, operation_id, "ready", expected_version, body, now)

async def bonus(factory, duel_id: str, actor_id: int, accept: bool, operation_id: str, tb_mods: Optional[list[str]] = None,
                now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step, duel, me, people):
        offered = await step.session.scalar(select(DuelBonus).where(DuelBonus.duel_id == duel.id).with_for_update())
        if duel.state != states.COMPLETED or offered is None or offered.state != "offered":
            raise DuelError("INVALID_STATE", await view(step.session, duel, actor_id))
        if not accept:
            offered.state = "cancelled"
            offered.finished_at = step.now
            await step.emit(duel, "bonus_declined", {"by": actor_id})
            step.command(duel, "close_room", {"duel": duel.id})
            return
        chosen = sorted(set(tb_mods or []) | {"NF"})
        if set(chosen) not in ({"NF"}, {"NF", "HD"}, {"NF", "HR"}, {"NF", "HD", "HR"}):
            raise DuelError("INVALID_STATE", await view(step.session, duel, actor_id))
        consents = sorted(set(offered.consents) | {actor_id})
        offered.consents = consents
        offered.tb_mods = {**(offered.tb_mods or {}), str(actor_id): chosen}
        await step.emit(duel, "bonus_accepted", {"by": actor_id})
        if len(consents) == len(people):
            offered.state = "setup"
            step.command(duel, "bonus_round", {"duel": duel.id})
    return await _act(factory, duel_id, actor_id, operation_id, "bonus", None, body, now)

async def give_up(factory, duel_id: str, actor_id: int, operation_id: str, expected_version: Optional[int] = None,
                  now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step, duel, me, people):
        if duel.state in states.TERMINAL or duel.state in states.UNCOMMITTED:
            raise DuelError("INVALID_STATE", await view(step.session, duel, actor_id))
        if duel.state == states.PLAYING:
            await step.move(duel, states.ABORTING, "surrender")
            step.command(duel, "abort_round", {"duel": duel.id})
        await _technical(step, duel, people, me, "surrender")
    return await _act(factory, duel_id, actor_id, operation_id, "give_up", expected_version, body, now)

System = Callable[[Step, Duel, list[DuelParticipant]], Awaitable[None]]

async def _system(factory, duel_id: str, allowed: tuple[str, ...], body: System, now: Optional[datetime]) -> Optional[dict[str, Any]]:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            duel = await session.scalar(select(Duel).where(Duel.id == duel_id).with_for_update())
            if duel is None or duel.state not in allowed:
                return None
            step = Step(session, None, now)
            await body(step, duel, await _participants(session, duel.id))
            await session.flush()
            return await view(session, duel, None)

async def pool_built(factory, duel_id: str, pool: Pool, catalog_version: str, analyzer_version: str,
                     now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        if duel.state == states.ACCEPTED:
            await step.move(duel, states.GENERATING_POOL)
        if pool.status == "ready":
            made = DuelPool(duel_id=duel.id, manifest_hash=pool.manifest_hash, catalog_version=catalog_version,
                            analyzer_version=analyzer_version, profile_versions={}, relaxation_level=pool.level, created_at=step.now)
            step.session.add(made)
            await step.session.flush()
            for choice in pool.entries:
                candidate = choice.candidate
                step.session.add(DuelPoolEntry(pool_id=made.id, slot=candidate.slot, beatmap_id=candidate.beatmap_id,
                                               revision_hash=candidate.revision, mods=list(SLOT_MODS[candidate.slot]),
                                               metrics_snapshot={"stars": candidate.stars, "cost": choice.cost, "parts": choice.parts},
                                               confidence=candidate.confidence, review_state=candidate.level))
            await step.move(duel, states.WAITING_ROOM)
            step.command(duel, "open_room", {"duel": duel.id})
        elif pool.status == "range_offered":
            event = await step.move(duel, states.RANGE_OFFERED, "range_offered")
            step.deadline(duel, "range_offer", PARAMETERS["range_offer_seconds"])
            step.notify(event, [p.player_id for p in people], {"kind": "range_offered", "duel": duel.id, "offer": pool.offer})
        else:
            event = await step.move(duel, states.CANCELLED, "pool_unavailable")
            step.notify(event, [p.player_id for p in people], {"kind": "cancelled", "duel": duel.id, "reason": "pool_unavailable",
                                                              "slots": pool.deficits})
    return await _system(factory, duel_id, (states.ACCEPTED, states.GENERATING_POOL), body, now)

async def room_ready(factory, duel_id: str, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        await step.move(duel, states.ROOM_SETUP)
        event = await step.move(duel, states.WAITING_PLAYERS)
        step.deadline(duel, "room_join", PARAMETERS["room_join_seconds"])
        step.notify(event, [p.player_id for p in people], {"kind": "room_ready", "duel": duel.id})
    return await _system(factory, duel_id, (states.WAITING_ROOM,), body, now)

async def players_present(factory, duel_id: str, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        pool, entries = await _pool(step.session, duel.id)
        await step.move(duel, states.PREPARING)
        pool.revealed_at = step.now
        for person in people:
            step.session.add(DuelPoolDelivery(pool_id=pool.id, player_id=person.player_id, manifest_hash=pool.manifest_hash,
                                              shown_at=step.now))
        from services.duels import catalog
        await catalog.lock(step.session, [entry.beatmap_id for entry in entries], step.now)
        step.deadline(duel, "pool_ack", PARAMETERS["pool_ack_seconds"])
    return await _system(factory, duel_id, (states.WAITING_PLAYERS,), body, now)

async def round_configured(factory, duel_id: str, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        for person in people:
            person.connection_state = "present"
        await step.move(duel, states.WAITING_READY)
        step.deadline(duel, "ready", PARAMETERS["ready_seconds"])
    return await _system(factory, duel_id, (states.ROUND_SETUP,), body, now)

async def round_started(factory, duel_id: str, match_id: int, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        current = (await _rounds(step.session, duel.id))[-1]
        current.state = "playing"
        attempt = await _attempt(step.session, current.id)
        attempt.match_id = match_id
        attempt.start_at = step.now
        await step.move(duel, states.PLAYING)
    return await _system(factory, duel_id, (states.STARTING,), body, now)

async def round_finished(factory, duel_id: str, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        current = (await _rounds(step.session, duel.id))[-1]
        attempt = await _attempt(step.session, current.id)
        attempt.end_at = step.now
        await step.move(duel, states.VERIFYING)
        step.deadline(duel, "evidence", PARAMETERS["evidence_wait_seconds"])
        step.command(duel, "verify_round", {"duel": duel.id, "attempt": attempt.id})
    return await _system(factory, duel_id, (states.PLAYING,), body, now)

async def round_verdict(factory, duel_id: str, verdict: dict[str, Any], now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        current = (await _rounds(step.session, duel.id))[-1]
        attempt = await _attempt(step.session, current.id)
        state = verdict["state"]
        if state == "pending":
            return
        for osu_id, score in (verdict.get("scores") or {}).items():
            step.session.add(DuelRoundScore(attempt_id=attempt.id, osu_user_id=int(osu_id), score_v2=int(score),
                                            mods=(verdict.get("mods") or {}).get(osu_id, []), evidence_id=verdict.get("evidence")))
        if verdict.get("game_id") is not None:
            attempt.game_id = verdict["game_id"]
        if state == "valid":
            attempt.validity = "valid"
            winner = _by_osu(people, int(verdict["winner"]))
            current.winner_id = winner.player_id
            current.state = "done"
            if winner.side == "A":
                duel.wins_a += 1
            else:
                duel.wins_b += 1
            await step.emit(duel, "round", {"ordinal": current.ordinal, "slot": current.slot, "winner": winner.player_id,
                                            "wins": {"A": duel.wins_a, "B": duel.wins_b}})
            await _next_round(step, duel, people)
            return
        if state == "tie":
            attempt.validity = "tie"
            current.tie_count += 1
            await step.emit(duel, "tie", {"ordinal": current.ordinal, "ties": current.tie_count})
            if current.tie_count > PARAMETERS["max_tie_replays"]:
                event = await step.move(duel, states.CANCELLED, "unresolved_tie")
                step.notify(event, [p.player_id for p in people], {"kind": "cancelled", "duel": duel.id, "reason": "unresolved_tie"})
                return
            await _new_attempt(step, duel, current)
            return
        reason = ",".join(verdict.get("reasons") or ["invalid"])
        earlier = await step.session.scalar(select(func.count()).select_from(DuelAttempt).where(
            DuelAttempt.round_id == current.id, DuelAttempt.validity == "invalid", DuelAttempt.invalid_reason == reason))
        attempt.validity = "invalid"
        attempt.invalid_reason = reason
        await step.emit(duel, "invalid", {"ordinal": current.ordinal, "reason": reason})
        if earlier:
            event = await step.move(duel, states.CANCELLED, "control_failure")
            step.notify(event, [p.player_id for p in people], {"kind": "cancelled", "duel": duel.id, "reason": "control_failure"})
            return
        await _new_attempt(step, duel, current)
    return await _system(factory, duel_id, (states.VERIFYING,), body, now)

async def player_lost(factory, duel_id: str, player_id: int, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        lost = next((p for p in people if p.player_id == player_id), None)
        if lost is None:
            return
        lost.connection_state = "lost"
        lost.disconnects = (lost.disconnects or 0) + 1
        if lost.disconnects > PARAMETERS["max_disconnect_recoveries"]:
            if duel.state == states.PLAYING:
                await step.move(duel, states.ABORTING, "disconnect")
                step.command(duel, "abort_round", {"duel": duel.id})
            await _technical(step, duel, people, lost, "disconnect")
            return
        if duel.state == states.PLAYING:
            await step.move(duel, states.ABORTING, "disconnect")
            step.command(duel, "abort_round", {"duel": duel.id})
            return
        duel.paused_from = duel.state
        duel.pause_reason = "disconnect"
        await step.move(duel, states.PAUSED, "disconnect")
        step.deadline(duel, "reconnect", PARAMETERS["reconnect_seconds"])
    live = (states.PREPARING, states.DRAFTING, states.SELECTING, states.ROUND_SETUP, states.WAITING_READY, states.STARTING,
            states.PLAYING)
    return await _system(factory, duel_id, live, body, now)

async def aborted(factory, duel_id: str, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        current = (await _rounds(step.session, duel.id))[-1]
        attempt = await _attempt(step.session, current.id)
        attempt.validity = "aborted"
        attempt.end_at = step.now
        duel.paused_from = states.ROUND_SETUP
        duel.pause_reason = "disconnect"
        await step.move(duel, states.PAUSED, "disconnect")
        step.deadline(duel, "reconnect", PARAMETERS["reconnect_seconds"])
    return await _system(factory, duel_id, (states.ABORTING,), body, now)

async def player_back(factory, duel_id: str, player_id: int, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    async def body(step, duel, people):
        for person in people:
            if person.player_id == player_id:
                person.connection_state = "present"
        if any(person.connection_state == "lost" for person in people):
            return
        resume = duel.paused_from or states.ROUND_SETUP
        duel.paused_from = None
        duel.pause_reason = None
        rounds = await _rounds(step.session, duel.id)
        if resume in (states.ROUND_SETUP, states.WAITING_READY, states.STARTING, states.PLAYING) and rounds:
            await _new_attempt(step, duel, rounds[-1])
            return
        await step.move(duel, resume)
        if resume in (states.DRAFTING, states.SELECTING):
            step.deadline(duel, "draft", PARAMETERS["draft_action_seconds"])
    return await _system(factory, duel_id, (states.PAUSED,), body, now)

async def on_deadline(step: Step, duel: Duel, kind: str) -> None:
    people = await _participants(step.session, duel.id)
    if kind == "draft" and duel.state in (states.DRAFTING, states.SELECTING):
        turns = await _turns(step.session, duel.id)
        action, side = turn_side(len(turns))
        actor = _player_of(people, side)
        if (actor.missed or 0) >= 1:
            duel.paused_from = duel.state
            duel.pause_reason = "absent"
            await step.move(duel, states.PAUSED, "absent")
            step.deadline(duel, "absence", PARAMETERS["reconnect_seconds"])
            return
        _, entries = await _pool(step.session, duel.id)
        choice = automatic_choice(duel.seed, len(turns), legal(entries, action))
        await _take_turn(step, duel, people, actor, choice, None, automatic=True)
    elif kind == "preparation" and duel.state == states.PREPARING:
        await step.move(duel, states.DRAFTING)
        step.deadline(duel, "draft", PARAMETERS["draft_action_seconds"])
    elif kind == "pool_ack" and duel.state == states.PREPARING:
        pool, _ = await _pool(step.session, duel.id)
        waiting = await step.session.scalar(select(func.count()).select_from(DuelPoolDelivery).where(
            DuelPoolDelivery.pool_id == pool.id, DuelPoolDelivery.acknowledged_at.is_(None)))
        if waiting:
            await _technical(step, duel, people, None, "pool_not_delivered")
    elif kind == "ready" and duel.state == states.WAITING_READY:
        idle = [person for person in people if person.connection_state != "ready"]
        await _technical(step, duel, people, idle[0] if len(idle) == 1 else None, "not_ready")
    elif kind in ("reconnect", "absence") and duel.state == states.PAUSED:
        absent = [person for person in people if person.connection_state == "lost" or (kind == "absence" and (person.missed or 0) >= 1)]
        await _technical(step, duel, people, absent[0] if len(absent) == 1 else None, kind)
    elif kind == "room_join" and duel.state == states.WAITING_PLAYERS:
        await _technical(step, duel, people, None, "room_join_timeout")
        step.command(duel, "close_room", {"duel": duel.id})
    elif kind == "evidence" and duel.state == states.VERIFYING:
        await _technical(step, duel, people, None, "evidence_missing")
    elif kind == "bonus" and duel.state == states.COMPLETED:
        offered = await step.session.scalar(select(DuelBonus).where(DuelBonus.duel_id == duel.id).with_for_update())
        if offered is not None and offered.state == "offered":
            offered.state = "cancelled"
            offered.finished_at = step.now
            await step.emit(duel, "bonus_expired", {})
            step.command(duel, "close_room", {"duel": duel.id})
    elif kind == "range_offer" and duel.state == states.RANGE_OFFERED:
        await _technical(step, duel, people, None, "range_declined")

MATCH_LINK = re.compile(r"^(?:https?://osu\.ppy\.sh/(?:community/matches|mp)/)?(\d{1,12})/?$")

def match_of(said: str) -> Optional[int]:
    found = MATCH_LINK.match(said.strip()) if isinstance(said, str) else None
    return int(found.group(1)) if found else None

async def external_room(factory, duel_id: str, actor_id: int, link: str, operation_id: str,
                        now: Optional[datetime] = None) -> dict[str, Any]:
    async def body(step, duel, me, people):
        match_id = match_of(link)
        if match_id is None:
            raise DuelError("ROOM_CONTROL_REQUIRED", await view(step.session, duel, actor_id))
        if duel.state != states.WAITING_ROOM:
            raise DuelError("INVALID_STATE", await view(step.session, duel, actor_id))
        held = await step.session.scalar(select(DuelRoom).where(DuelRoom.match_id == match_id,
                                                                DuelRoom.status.notin_(["closed", "released"])))
        if held is not None and held.duel_id != duel.id:
            raise DuelError("ROOM_CONTROL_REQUIRED", await view(step.session, duel, actor_id))
        if held is None:
            held = DuelRoom(duel_id=duel.id, room_name=f"external {match_id}", match_id=match_id, channel=f"#mp_{match_id}",
                            origin="external", status="proposed", capabilities={"confirmed": []}, created_at=step.now)
            step.session.add(held)
            await step.session.flush()
        confirmed = sorted(set((held.capabilities or {}).get("confirmed", [])) | {actor_id})
        held.capabilities = {**(held.capabilities or {}), "confirmed": confirmed}
        await step.emit(duel, "external_room", {"match": match_id, "by": actor_id})
        if len(confirmed) == len(people) and held.status == "proposed":
            held.status = "checking"
            step.command(duel, "adopt_room", {"duel": duel.id, "room": held.id})
    return await _act(factory, duel_id, actor_id, operation_id, "external_room", None, body, now)

async def note(factory, duel_id: str, kind: str, payload: dict[str, Any], now: Optional[datetime] = None) -> None:
    async def body(step, duel, people):
        await step.emit(duel, kind, payload)
    every = tuple(states.TRANSITIONS)
    await _system(factory, duel_id, every, body, now)
