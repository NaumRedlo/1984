from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel import Duel, DuelCommand, DuelGlicko, DuelLease, DuelParticipant, DuelQueueEntry
from db.models.player import Player
from services.duels import deadlines, matchmaker, service, states
from services.duels.matchmaker import Seeker

NOW = datetime(2026, 10, 23, 12, 0, 0)

def _seeker(player, rating=1500.0, theta=None, waited=0):
    return Seeker(player, player, NOW - timedelta(seconds=waited), rating, theta)

def test_windows_widen_with_waiting():
    assert matchmaker.window(10) == (0.40, 0.60, 0.6)
    assert matchmaker.window(90) == (0.35, 0.65, 0.8)
    assert matchmaker.window(500) == (0.30, 0.70, 1.0)

def test_a_pair_is_priced_by_rating_skill_and_repeats():
    assert matchmaker.price(_seeker(1, 1500, 5.0), _seeker(2, 1500, 5.0), 0, False) == pytest.approx(0.0)
    assert matchmaker.price(_seeker(1, 1500, 5.0), _seeker(2, 1500, 5.4), 0, True) == pytest.approx(0.5 * 0.4 + 0.2)
    assert matchmaker.price(_seeker(1, 1500), _seeker(2, 1640), 10, False) is None
    assert matchmaker.price(_seeker(1, 1500), _seeker(2, 1640), 500, False) is not None
    assert matchmaker.price(_seeker(1, 1500), _seeker(2, 1700), 500, False) is None
    assert matchmaker.price(_seeker(1, 1500, 5.0), _seeker(2, 1500, 5.9), 10, False) is None
    assert matchmaker.price(_seeker(1, 1500, 5.9), _seeker(2, 1500, 5.0), 500, False) is not None
    one_known = matchmaker.price(_seeker(1, 1500, 2.8), _seeker(2, 1500, None), 0, False)
    assert one_known == pytest.approx(matchmaker.UNKNOWN_PENALTY)
    assert matchmaker.price(_seeker(1, 1500, 6.0), _seeker(2, 1500, None), 0, False) == pytest.approx(matchmaker.UNKNOWN_PENALTY)

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add_all([Player(id=n, osu_user_id=100 + n, osu_username=f"p{n}") for n in (1, 2, 3, 4)])
        await session.commit()
    yield made
    await engine.dispose()

async def _queue(factory, player, at):
    await service.enter_queue(factory, player, f"queue-{player}-{at.isoformat()}", now=at)

async def test_the_longest_waiting_player_is_offered_a_close_opponent(factory):
    await _queue(factory, 1, NOW - timedelta(seconds=40))
    await _queue(factory, 2, NOW - timedelta(seconds=20))
    async with factory() as session:
        session.add(DuelGlicko(player_id=3, version="glicko2-v1", rating=2100, rd=60, sigma=0.06))
        await session.commit()
    await _queue(factory, 3, NOW - timedelta(seconds=30))
    duel_id = await matchmaker.offer(factory, now=NOW)
    async with factory() as session:
        people = sorted((await session.scalars(select(DuelParticipant.player_id).where(DuelParticipant.duel_id == duel_id))).all())
        duel = await session.get(Duel, duel_id)
        assert people == [1, 2] and (duel.state, duel.mode) == (states.MATCH_OFFERED, "rated")
        assert await session.scalar(select(func.count()).select_from(DuelLease).where(DuelLease.duel_id == duel_id)) == 2
    assert await matchmaker.offer(factory, now=NOW) is None

async def test_absent_players_are_not_matched(factory):
    await _queue(factory, 1, NOW)
    await _queue(factory, 2, NOW)
    assert await matchmaker.offer(factory, present=lambda player: player != 2, now=NOW) is None

async def test_both_confirm_and_the_duel_starts_building_its_pool(factory):
    await _queue(factory, 1, NOW)
    await _queue(factory, 2, NOW)
    duel_id = await matchmaker.offer(factory, now=NOW)
    first = await service.accept(factory, duel_id, 1, "yes-1", expected_version=1, now=NOW)
    assert first["state"] == states.MATCH_OFFERED and first["actions"] == []
    assert (await service.read(factory, duel_id, 2))["actions"] == ["accept", "decline"]
    both = await service.accept(factory, duel_id, 2, "yes-2", expected_version=1, now=NOW)
    assert both["state"] == states.ACCEPTED
    async with factory() as session:
        assert (await session.scalar(select(DuelCommand.kind))) == "generate_pool"

async def test_the_one_who_accepted_returns_with_the_old_waiting_time(factory):
    start = NOW - timedelta(seconds=100)
    await _queue(factory, 1, start)
    await _queue(factory, 2, NOW)
    duel_id = await matchmaker.offer(factory, now=NOW)
    await service.accept(factory, duel_id, 1, "yes-1", now=NOW)
    gone = await service.decline(factory, duel_id, 2, "no-2", now=NOW)
    assert (gone["state"], gone["outcome"]) == (states.CANCELLED, "offer_declined")
    async with factory() as session:
        back = await session.scalar(select(DuelQueueEntry).where(DuelQueueEntry.player_id == 1))
        assert (back.status, back.enqueued_at) == ("waiting", start)
        lease = await session.get(DuelLease, 1)
        assert lease.kind == "queue" and await session.get(DuelLease, 2) is None
        left = await session.scalar(select(DuelQueueEntry).where(DuelQueueEntry.player_id == 2))
        assert left.status == "left"

async def test_an_unconfirmed_offer_expires_after_thirty_seconds(factory):
    await _queue(factory, 1, NOW)
    await _queue(factory, 2, NOW)
    duel_id = await matchmaker.offer(factory, now=NOW)
    await service.accept(factory, duel_id, 2, "yes-2", now=NOW)
    await deadlines.sweep(factory, NOW + timedelta(seconds=31))
    assert (await service.read(factory, duel_id, 1))["outcome"] == "offer_expired"
    async with factory() as session:
        assert (await session.get(DuelLease, 2)).kind == "queue" and await session.get(DuelLease, 1) is None

async def test_a_third_rated_meeting_within_a_day_is_not_arranged(factory):
    for number in range(2):
        async with factory() as session:
            duel = Duel(id=f"old{number}", mode="rated", state=states.COMPLETED, rules_version="duels-v1", seed="s",
                        created_at=NOW - timedelta(hours=number + 2))
            session.add(duel)
            await session.flush()
            session.add_all([DuelParticipant(duel_id=duel.id, player_id=1, osu_user_id=101, side="A"),
                             DuelParticipant(duel_id=duel.id, player_id=2, osu_user_id=102, side="B")])
            await session.commit()
    await _queue(factory, 1, NOW)
    await _queue(factory, 2, NOW)
    assert await matchmaker.offer(factory, now=NOW) is None
    await _queue(factory, 3, NOW)
    assert await matchmaker.offer(factory, now=NOW) is not None


async def test_pool_failure_allows_requeue_and_the_next_offer_uses_new_entries(factory):
    from services.duels import generator, series

    for player in (1, 2):
        await _queue(factory, player, NOW)
    first = await matchmaker.offer(factory, now=NOW)
    for player in (1, 2):
        await service.accept(factory, first, player, f"first-accept-{player}", now=NOW)
    gone = await series.pool_built(factory, first, generator.Pool("unavailable", deficits=["HR2"]), "c", "f", NOW)
    assert gone["outcome"] == "pool_unavailable"
    for player in (1, 2):
        assert await service.queue_state(factory, player) is None
        await _queue(factory, player, NOW + timedelta(seconds=10))
    second = await matchmaker.offer(factory, now=NOW + timedelta(seconds=11))
    assert second is not None and second != first
    await service.accept(factory, second, 1, "second-accept-1", now=NOW)
    await service.decline(factory, second, 2, "second-decline-2", now=NOW)
    waiting = await service.queue_state(factory, 1)
    assert waiting["since"] == service._stamp(NOW + timedelta(seconds=10))
    again = await service.enter_queue(factory, 1, "retry-search", now=NOW + timedelta(seconds=20))
    assert again["queue"] == waiting
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(DuelQueueEntry).where(
            DuelQueueEntry.player_id == 1, DuelQueueEntry.status == "waiting")) == 1
        assert await session.scalar(select(func.count()).select_from(DuelQueueEntry).where(DuelQueueEntry.status == "offered")) == 0


async def test_a_pair_that_declined_its_star_range_rests_before_meeting_again(factory):
    from services.duels import generator, series

    for player in (1, 2):
        await _queue(factory, player, NOW)
    first = await matchmaker.offer(factory, now=NOW)
    for player in (1, 2):
        await service.accept(factory, first, player, f"range-accept-{player}", now=NOW)
    await series.pool_built(factory, first, generator.Pool("range_offered", 4, offer={"NM1": (4.6, 5.4)}), "c", "f", NOW)
    await deadlines.sweep(factory, NOW + timedelta(seconds=61))
    async with factory() as session:
        assert (await session.get(Duel, first)).outcome_reason == "range_declined"
    for player in (1, 2):
        await _queue(factory, player, NOW + timedelta(seconds=70))
    assert await matchmaker.offer(factory, now=NOW + timedelta(seconds=71)) is None, "the same offer would be declined again"
    later = await matchmaker.offer(factory, now=NOW + matchmaker.NO_POOL_REST + timedelta(seconds=1))
    assert later is not None and later != first

async def test_legacy_offered_entries_are_closed_when_starting_a_new_search(factory):
    async with factory() as session:
        session.add(DuelQueueEntry(player_id=1, mode="rated", status="offered", enqueued_at=NOW - timedelta(days=1)))
        session.add(DuelLease(player_id=1, kind="queue", since=NOW))
        await session.commit()
    await _queue(factory, 1, NOW)
    await _queue(factory, 2, NOW)
    duel_id = await matchmaker.offer(factory, now=NOW)
    await service.accept(factory, duel_id, 1, "legacy-accept", now=NOW)
    await service.decline(factory, duel_id, 2, "legacy-decline", now=NOW)
    assert (await service.queue_state(factory, 1))["since"] == service._stamp(NOW)


async def test_orphan_queue_rows_do_not_block_a_valid_pair(factory):
    async with factory() as session:
        session.add(DuelQueueEntry(player_id=3, mode="rated", status="waiting", enqueued_at=NOW - timedelta(hours=1)))
        await session.commit()
    await _queue(factory, 1, NOW)
    await _queue(factory, 2, NOW)
    duel_id = await matchmaker.offer(factory, now=NOW)
    assert duel_id is not None
    async with factory() as session:
        assert set((await session.scalars(select(DuelParticipant.player_id).where(DuelParticipant.duel_id == duel_id))).all()) == {1, 2}
