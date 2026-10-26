import asyncio
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel import Duel, DuelCommand, DuelDeadline, DuelEvent, DuelLease, DuelQueueEntry, DuelNotification, DuelOperation
from db.models.player import Player
from services.duels import deadlines, outbox, service, states
from services.duels.errors import DuelError

NOW = datetime(2026, 10, 23, 12, 0, 0)

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'duels.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        for number in range(1, 5):
            session.add(Player(id=number, osu_user_id=1000 + number, osu_username=f"player{number}"))
        session.add(Player(id=9, osu_user_id=0, osu_username="unlinked"))
        await session.commit()
    yield made
    await engine.dispose()

async def _count(factory, model, *where):
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(model).where(*where))

async def _code(call) -> str:
    with pytest.raises(DuelError) as caught:
        await call
    return caught.value.code

async def test_an_invitation_waits_for_the_other_player_and_both_get_their_side(factory):
    duel = await service.invite(factory, 1, 2, "op-invite", now=NOW)
    assert duel["state"] == states.INVITED and duel["version"] == 1 and duel["mode"] == "direct"
    assert sorted(person["side"] for person in duel["participants"]) == ["A", "B"]
    assert duel["actions"] == ["decline"]
    assert duel["deadlines"] == [{"kind": "invitation", "at": (NOW + timedelta(seconds=120)).isoformat() + "Z"}]
    seen = await service.read(factory, duel["id"], 2)
    assert seen["actions"] == ["accept", "decline"]
    assert await _count(factory, DuelNotification, DuelNotification.player_id == 2) == 2
    assert await _count(factory, DuelLease) == 0

async def test_invitations_refuse_oneself_strangers_and_a_second_pending_one(factory):
    assert await _code(service.invite(factory, 1, 1, "self", now=NOW)) == "SELF_CHALLENGE"
    assert await _code(service.invite(factory, 1, 9, "unlinked", now=NOW)) == "NOT_LINKED"
    assert await _code(service.invite(factory, 1, 42, "missing", now=NOW)) == "NOT_LINKED"
    await service.invite(factory, 1, 2, "first", now=NOW)
    assert await _code(service.invite(factory, 2, 1, "back", now=NOW)) == "ALREADY_INVITED"

async def test_accepting_holds_both_players_and_asks_for_a_pool(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    accepted = await service.accept(factory, duel["id"], 2, "accept", expected_version=1, now=NOW)
    assert accepted["state"] == states.ACCEPTED and accepted["version"] == 2
    assert accepted["actions"] == ["surrender"]
    assert await _count(factory, DuelLease, DuelLease.duel_id == duel["id"]) == 2
    async with factory() as session:
        command = await session.scalar(select(DuelCommand))
        assert (command.kind, command.payload) == ("generate_pool", {"duel": duel["id"], "version": 2})
    assert await _code(service.enter_queue(factory, 1, "queue", now=NOW)) == "ALREADY_BUSY"

async def test_only_the_invited_player_accepts_and_only_once(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    assert await _code(service.accept(factory, duel["id"], 1, "self-accept", now=NOW)) == "INVALID_STATE"
    assert await _code(service.accept(factory, duel["id"], 3, "outsider", now=NOW)) == "NOT_PARTICIPANT"
    assert await _code(service.accept(factory, "nope", 2, "missing", now=NOW)) == "NOT_FOUND"
    await service.accept(factory, duel["id"], 2, "accept", now=NOW)
    assert await _code(service.accept(factory, duel["id"], 2, "again", now=NOW)) == "INVALID_STATE"

async def test_repeating_an_operation_returns_its_first_answer_and_changes_nothing(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    first = await service.accept(factory, duel["id"], 2, "accept-once", expected_version=1, now=NOW)
    again = await service.accept(factory, duel["id"], 2, "accept-once", expected_version=1, now=NOW + timedelta(seconds=5))
    assert again == first
    assert await _count(factory, DuelEvent, DuelEvent.duel_id == duel["id"]) == 2
    assert await _count(factory, DuelCommand) == 1
    assert await _code(service.decline(factory, duel["id"], 2, "accept-once", now=NOW)) == "OPERATION_REUSED"
    assert await _code(service.accept(factory, duel["id"], 3, "accept-once", now=NOW)) == "OPERATION_REUSED"

async def test_a_stale_version_gets_a_conflict_with_the_current_state(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    await service.accept(factory, duel["id"], 2, "accept", expected_version=1, now=NOW)
    with pytest.raises(DuelError) as caught:
        await service.surrender(factory, duel["id"], 1, "late", expected_version=1, now=NOW)
    assert caught.value.code == "VERSION_CONFLICT"
    assert caught.value.snapshot["version"] == 2 and caught.value.snapshot["state"] == states.ACCEPTED
    assert caught.value.body()["retryable"] is True

async def test_two_parallel_accepts_never_put_one_player_in_two_duels(factory):
    first = await service.invite(factory, 1, 2, "a", now=NOW)
    second = await service.invite(factory, 3, 2, "b", now=NOW)
    results = await asyncio.gather(
        service.accept(factory, first["id"], 2, "accept-a", now=NOW),
        service.accept(factory, second["id"], 2, "accept-b", now=NOW),
        return_exceptions=True,
    )
    won = [result for result in results if isinstance(result, dict)]
    lost = [result for result in results if isinstance(result, DuelError)]
    assert len(won) == 1 and len(lost) == 1 and lost[0].code == "ALREADY_BUSY"
    assert await _count(factory, DuelLease, DuelLease.player_id == 2) == 1
    assert await _count(factory, Duel, Duel.state == states.ACCEPTED) == 1

async def test_declining_or_withdrawing_ends_the_invitation(factory):
    first = await service.invite(factory, 1, 2, "a", now=NOW)
    declined = await service.decline(factory, first["id"], 2, "no", now=NOW)
    assert (declined["state"], declined["outcome"], declined["actions"]) == (states.DECLINED, "declined", [])
    second = await service.invite(factory, 1, 2, "b", now=NOW)
    withdrawn = await service.decline(factory, second["id"], 1, "back", now=NOW)
    assert withdrawn["outcome"] == "withdrawn"
    assert await _code(service.accept(factory, second["id"], 2, "late", now=NOW)) == "INVALID_STATE"

async def test_refusing_before_the_first_round_cancels_without_a_result_and_frees_both(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    await service.accept(factory, duel["id"], 2, "accept", now=NOW)
    cancelled = await service.surrender(factory, duel["id"], 1, "give-up", now=NOW)
    assert (cancelled["state"], cancelled["outcome"], cancelled["winner"]) == (states.CANCELLED, "refused_before_start", None)
    assert await _count(factory, DuelLease) == 0
    assert (await service.enter_queue(factory, 1, "queue", now=NOW))["queue"]["status"] == "waiting"

async def test_an_unanswered_invitation_expires_once_and_a_stale_deadline_does_nothing(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    assert await deadlines.sweep(factory, NOW + timedelta(seconds=119)) == 0
    assert await deadlines.sweep(factory, NOW + timedelta(seconds=121)) == 1
    assert (await service.read(factory, duel["id"], 1))["state"] == states.EXPIRED
    async with factory() as session:
        deadline_id = await session.scalar(select(DuelDeadline.id))
    assert await deadlines.process(factory, deadline_id, NOW + timedelta(seconds=200)) == "done"
    assert await _count(factory, DuelEvent, DuelEvent.duel_id == duel["id"]) == 2

    answered = await service.invite(factory, 1, 2, "again", now=NOW)
    await service.accept(factory, answered["id"], 2, "yes", now=NOW)
    assert await deadlines.sweep(factory, NOW + timedelta(seconds=300)) == 0
    async with factory() as session:
        outcome = await session.scalar(select(DuelDeadline.outcome).where(DuelDeadline.duel_id == answered["id"]))
    assert outcome == "stale"
    assert (await service.read(factory, answered["id"], 2))["state"] == states.ACCEPTED

async def test_events_are_numbered_without_gaps_and_can_be_read_after_a_cursor(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    await service.accept(factory, duel["id"], 2, "accept", now=NOW)
    await service.surrender(factory, duel["id"], 2, "leave", now=NOW)
    events = await service.events_after(factory, duel["id"], 1, 0)
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert [event["type"] for event in events] == ["invited", "state", "state"]
    assert [event["payload"].get("to") for event in events[1:]] == [states.ACCEPTED, states.CANCELLED]
    assert [event["sequence"] for event in await service.events_after(factory, duel["id"], 2, 2)] == [3]
    assert await _code(service.events_after(factory, duel["id"], 3, 0)) == "NOT_PARTICIPANT"

async def test_the_queue_holds_one_place_and_lets_go(factory):
    entered = await service.enter_queue(factory, 1, "in", now=NOW)
    assert await service.enter_queue(factory, 1, "in-again", now=NOW) == entered
    assert await _count(factory, DuelQueueEntry, DuelQueueEntry.status == "waiting") == 1
    assert await service.busy(factory, [1, 2]) == {1}
    invited = await service.invite(factory, 2, 1, "call", now=NOW)
    assert await _code(service.accept(factory, invited["id"], 1, "take", now=NOW)) == "ALREADY_BUSY"
    assert (await service.leave_queue(factory, 1, "out", now=NOW)) == {"queue": None}
    assert await _code(service.leave_queue(factory, 1, "out-again", now=NOW)) == "INVALID_STATE"
    assert (await service.accept(factory, invited["id"], 1, "take-now", now=NOW))["state"] == states.ACCEPTED

async def test_a_technical_cancel_ends_any_live_duel_once(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    await service.accept(factory, duel["id"], 2, "accept", now=NOW)
    cancelled = await service.cancel(factory, duel["id"], "service_recovering", now=NOW)
    assert (cancelled["state"], cancelled["outcome"]) == (states.CANCELLED, "service_recovering")
    assert await service.cancel(factory, duel["id"], "service_recovering", now=NOW) is None
    assert await _count(factory, DuelLease) == 0

async def test_my_live_duels_leave_out_finished_ones(factory):
    live = await service.invite(factory, 1, 2, "live", now=NOW)
    gone = await service.invite(factory, 1, 3, "gone", now=NOW)
    await service.decline(factory, gone["id"], 3, "no", now=NOW)
    assert [duel["id"] for duel in await service.mine(factory, 1)] == [live["id"]]

async def test_outbox_runs_a_command_outside_the_transaction_and_retries_failures(factory, monkeypatch):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    await service.accept(factory, duel["id"], 2, "accept", now=NOW)
    seen = []

    async def flaky(command):
        seen.append(command["attempts"])
        if len(seen) == 1:
            raise RuntimeError("bancho is away")

    monkeypatch.setitem(outbox.HANDLERS, "generate_pool", flaky)
    assert await outbox.sweep(factory, NOW) == 0
    async with factory() as session:
        command = await session.scalar(select(DuelCommand))
        assert (command.status, command.attempts, command.last_error) == ("pending", 1, "RuntimeError: bancho is away")
    assert await outbox.sweep(factory, NOW + timedelta(seconds=1)) == 0
    assert await outbox.sweep(factory, NOW + timedelta(seconds=3)) == 1
    assert seen == [1, 2]
    async with factory() as session:
        assert (await session.scalar(select(DuelCommand.status))) == "done"
    assert await outbox.sweep(factory, NOW + timedelta(minutes=10)) == 0

async def test_a_claimed_command_left_by_a_crash_is_taken_again_after_its_claim(factory, monkeypatch):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    await service.accept(factory, duel["id"], 2, "accept", now=NOW)

    async def fine(_command):
        return None

    monkeypatch.setitem(outbox.HANDLERS, "generate_pool", fine)
    taken = await outbox.claim(factory, NOW)
    assert len(taken) == 1 and await outbox.claim(factory, NOW + timedelta(seconds=30)) == []
    again = await outbox.claim(factory, NOW + timedelta(seconds=61))
    assert again[0]["attempts"] == 2
    assert await outbox.settle(factory, taken[0]["id"], taken[0]["attempts"], None) == "lost"
    assert await outbox.settle(factory, again[0]["id"], again[0]["attempts"], None) == "done"

async def test_nothing_is_claimed_while_no_command_has_a_handler(factory):
    duel = await service.invite(factory, 1, 2, "invite", now=NOW)
    await service.accept(factory, duel["id"], 2, "accept", now=NOW)
    assert await outbox.claim(factory, NOW) == []
    assert await _count(factory, DuelOperation) == 2
