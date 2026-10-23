from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel import DuelAttempt, DuelCommand, DuelLease
from db.models.player import Player
from services.duels import deadlines, generator, series, service, states
from services.duels.errors import DuelError
from services.duels.generator import Candidate, Skill
from services.duels.rules import SLOTS

NOW = datetime(2026, 10, 23, 12, 0, 0)

class Clock:
    def __init__(self):
        self.now = NOW
        self.ops = 0

    def tick(self, seconds: float = 1) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now

    def op(self) -> str:
        self.ops += 1
        return f"op-{self.ops}"

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'series.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add_all([Player(id=1, osu_user_id=101, osu_username="one"), Player(id=2, osu_user_id=102, osu_username="two")])
        await session.commit()
    yield made
    await engine.dispose()

def _pool():
    maps = []
    for number, slot in enumerate(SLOTS, start=1):
        for extra in range(3):
            beatmap = number * 10 + extra
            maps.append(Candidate(beatmap, beatmap, f"m{beatmap}", slot, 5.0 + 0.05 * extra, "confident", 0.9, f"{beatmap:032x}"))
    profile = {slot: Skill(5.0, 0.4) for slot in SLOTS if slot != "TB"}
    return generator.generate(maps, profile, profile, "seed")

async def _drafting(factory, clock):
    invited = await service.invite(factory, 1, 2, clock.op(), now=clock.now)
    duel_id = invited["id"]
    await service.accept(factory, duel_id, 2, clock.op(), now=clock.tick())
    pool = _pool()
    assert (await series.pool_built(factory, duel_id, pool, "catalog-1", "features-v1", clock.tick()))["state"] == states.WAITING_ROOM
    hidden = await _view(factory, duel_id, 1)
    assert hidden["pool"] is None
    await series.room_ready(factory, duel_id, clock.tick())
    await series.players_present(factory, duel_id, clock.tick())
    shown = await _view(factory, duel_id, 1)
    assert len(shown["pool"]["entries"]) == 9 and "pool_ack" in shown["actions"]
    for player in (1, 2):
        await series.pool_ack(factory, duel_id, player, pool.manifest_hash, clock.op(), now=clock.tick())
    await deadlines.sweep(factory, clock.tick(121))
    assert (await _view(factory, duel_id, 1))["state"] == states.DRAFTING
    return duel_id

async def _view(factory, duel_id, viewer):
    async with factory() as session:
        from db.models.duel import Duel
        return await series.view(session, await session.get(Duel, duel_id), viewer)

def _player(duel, side):
    return next(p["player"] for p in duel["participants"] if p["side"] == side)

def _osu(duel, player):
    return next(p["osu_user_id"] for p in duel["participants"] if p["player"] == player)

async def _ban_all(factory, clock, duel_id):
    for index, side in enumerate(series.BANS):
        duel = await _view(factory, duel_id, 1)
        free = [e["slot"] for e in duel["pool"]["entries"] if e["slot"] != "TB" and not e["banned_by"]]
        await series.draft(factory, duel_id, _player(duel, side), free[0], clock.op(), now=clock.tick())
    return await _view(factory, duel_id, 1)

async def _play(factory, clock, duel_id, winner_side, tb_mods=None):
    duel = await _view(factory, duel_id, 1)
    if duel["state"] == states.SELECTING:
        _, side = series.turn_side(len(duel["draft"]))
        free = [e["slot"] for e in duel["pool"]["entries"] if e["slot"] != "TB" and not e["banned_by"] and not e["used"]]
        await series.draft(factory, duel_id, _player(duel, side), free[0], clock.op(), now=clock.tick())
    await series.round_configured(factory, duel_id, clock.tick())
    for player in (1, 2):
        await series.ready(factory, duel_id, player, clock.op(), tb_mods=tb_mods, now=clock.tick())
    await series.round_started(factory, duel_id, 777, clock.tick())
    await series.round_finished(factory, duel_id, clock.tick(200))
    winner = _player(duel, winner_side)
    loser = 3 - winner
    return await series.round_verdict(factory, duel_id, {"state": "valid", "winner": _osu(duel, winner),
                                                         "scores": {str(_osu(duel, winner)): 900000, str(_osu(duel, loser)): 800000}},
                                      clock.tick())

async def test_a_series_runs_from_the_pool_to_three_wins(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    after_bans = await _ban_all(factory, clock, duel_id)
    assert after_bans["state"] == states.SELECTING and sum(1 for e in after_bans["pool"]["entries"] if e["banned_by"]) == 4
    for side in ("A", "B", "A"):
        await _play(factory, clock, duel_id, side)
    done = await _play(factory, clock, duel_id, "A")
    assert (done["state"], done["wins"], done["outcome"]) == (states.COMPLETED, {"A": 3, "B": 1}, "played")
    assert done["winner"] == _player(done, "A")
    assert [r["picked_by"] for r in done["rounds"]] == [_player(done, s) for s in ("B", "A", "B", "A")]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(DuelLease)) == 0

async def test_two_all_goes_to_the_tiebreaker_where_each_chooses_mods(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _ban_all(factory, clock, duel_id)
    for side in ("A", "B", "A"):
        await _play(factory, clock, duel_id, side)
    level = await _play(factory, clock, duel_id, "B")
    assert level["state"] == states.ROUND_SETUP and level["rounds"][-1]["slot"] == "TB" and level["rounds"][-1]["picked_by"] is None
    await series.round_configured(factory, duel_id, clock.tick())
    with pytest.raises(DuelError) as wrong:
        await series.ready(factory, duel_id, 1, clock.op(), tb_mods=["DT"], now=clock.tick())
    assert wrong.value.code == "INVALID_STATE"
    await series.ready(factory, duel_id, 1, clock.op(), tb_mods=["HD", "HR"], now=clock.tick())
    await series.ready(factory, duel_id, 2, clock.op(), tb_mods=[], now=clock.tick())
    await series.round_started(factory, duel_id, 777, clock.tick())
    await series.round_finished(factory, duel_id, clock.tick())
    duel = await _view(factory, duel_id, 1)
    final = await series.round_verdict(factory, duel_id, {"state": "valid", "winner": _osu(duel, _player(duel, "B")), "scores": {}},
                                       clock.tick())
    assert (final["state"], final["wins"]) == (states.COMPLETED, {"A": 2, "B": 3})

async def test_turns_belong_to_their_side_and_banned_or_tiebreak_maps_cannot_be_taken(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    duel = await _view(factory, duel_id, 1)
    with pytest.raises(DuelError) as early:
        await series.draft(factory, duel_id, _player(duel, "B"), "NM1", clock.op(), now=clock.tick())
    assert early.value.code == "NOT_YOUR_TURN"
    with pytest.raises(DuelError) as tb:
        await series.draft(factory, duel_id, _player(duel, "A"), "TB", clock.op(), now=clock.tick())
    assert tb.value.code == "SLOT_UNAVAILABLE"
    await series.draft(factory, duel_id, _player(duel, "A"), "NM1", clock.op(), now=clock.tick())
    with pytest.raises(DuelError) as twice:
        await series.draft(factory, duel_id, _player(duel, "B"), "NM1", clock.op(), now=clock.tick())
    assert twice.value.code == "SLOT_UNAVAILABLE"
    assert "ban" in (await _view(factory, duel_id, _player(duel, "B")))["actions"]

async def test_a_missed_turn_is_chosen_by_the_seed_and_a_second_miss_pauses_then_cancels(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    assert await deadlines.sweep(factory, clock.tick(31)) == 1
    duel = await _view(factory, duel_id, 1)
    first = duel["draft"][0]
    assert first["automatic"] and first["by"] == _player(duel, "A")
    expected = series.automatic_choice(await _seed(factory, duel_id), 0, [s for s in SLOTS if s != "TB"])
    assert first["slot"] == expected
    await series.draft(factory, duel_id, _player(duel, "B"), [s for s in SLOTS if s not in ("TB", expected)][0], clock.op(),
                       now=clock.tick())
    assert await deadlines.sweep(factory, clock.tick(31)) == 1
    paused = await _view(factory, duel_id, 1)
    assert paused["state"] == states.PAUSED
    assert await deadlines.sweep(factory, clock.tick(121)) == 1
    gone = await _view(factory, duel_id, 1)
    assert (gone["state"], gone["outcome"], gone["winner"]) == (states.CANCELLED, "absence", None)

async def _seed(factory, duel_id):
    async with factory() as session:
        from db.models.duel import Duel
        return (await session.get(Duel, duel_id)).seed

async def test_a_tie_replays_the_same_map_and_a_third_tie_cancels(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _ban_all(factory, clock, duel_id)
    duel = await _view(factory, duel_id, 1)
    _, side = series.turn_side(len(duel["draft"]))
    await series.draft(factory, duel_id, _player(duel, side), [e["slot"] for e in duel["pool"]["entries"]
                                                                if e["slot"] != "TB" and not e["banned_by"]][0], clock.op(), now=clock.tick())
    for tie in range(1, 4):
        await series.round_configured(factory, duel_id, clock.tick())
        for player in (1, 2):
            await series.ready(factory, duel_id, player, clock.op(), now=clock.tick())
        await series.round_started(factory, duel_id, 777, clock.tick())
        await series.round_finished(factory, duel_id, clock.tick())
        after = await series.round_verdict(factory, duel_id, {"state": "tie", "scores": {}}, clock.tick())
        assert after["rounds"][-1]["ties"] == tie and len(after["rounds"]) == 1
    assert (after["state"], after["outcome"]) == (states.CANCELLED, "unresolved_tie")
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(DuelAttempt)) == 3

async def test_a_broken_setup_is_retried_once_and_the_same_fault_stops_the_series(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _ban_all(factory, clock, duel_id)
    duel = await _view(factory, duel_id, 1)
    _, side = series.turn_side(len(duel["draft"]))
    await series.draft(factory, duel_id, _player(duel, side), [e["slot"] for e in duel["pool"]["entries"]
                                                                if e["slot"] != "TB" and not e["banned_by"]][0], clock.op(), now=clock.tick())
    outcomes = []
    for _ in range(2):
        await series.round_configured(factory, duel_id, clock.tick())
        for player in (1, 2):
            await series.ready(factory, duel_id, player, clock.op(), now=clock.tick())
        await series.round_started(factory, duel_id, 777, clock.tick())
        await series.round_finished(factory, duel_id, clock.tick())
        outcomes.append(await series.round_verdict(factory, duel_id, {"state": "invalid", "reasons": ["no_fail:102"]}, clock.tick()))
    assert outcomes[0]["state"] == states.ROUND_SETUP
    assert (outcomes[1]["state"], outcomes[1]["outcome"]) == (states.CANCELLED, "control_failure")

async def test_a_lost_player_stops_the_map_returns_for_a_replay_and_loses_after_the_third_time(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _ban_all(factory, clock, duel_id)
    await _play(factory, clock, duel_id, "A")
    duel = await _view(factory, duel_id, 1)
    _, side = series.turn_side(len(duel["draft"]))
    await series.draft(factory, duel_id, _player(duel, side), [e["slot"] for e in duel["pool"]["entries"]
                                                                if e["slot"] != "TB" and not e["banned_by"] and not e["used"]][0],
                       clock.op(), now=clock.tick())
    for attempt in range(2):
        await series.round_configured(factory, duel_id, clock.tick())
        for player in (1, 2):
            await series.ready(factory, duel_id, player, clock.op(), now=clock.tick())
        await series.round_started(factory, duel_id, 777, clock.tick())
        assert (await series.player_lost(factory, duel_id, 2, clock.tick()))["state"] == states.ABORTING
        assert (await series.aborted(factory, duel_id, clock.tick()))["state"] == states.PAUSED
        back = await series.player_back(factory, duel_id, 2, clock.tick())
        assert back["state"] == states.ROUND_SETUP
    async with factory() as session:
        commands = (await session.scalars(select(DuelCommand.kind))).all()
    assert commands.count("abort_round") == 2
    await series.round_configured(factory, duel_id, clock.tick())
    final = await series.player_lost(factory, duel_id, 2, clock.tick())
    assert (final["state"], final["outcome"], final["winner"]) == (states.COMPLETED, "disconnect", 1)
    assert final["wins"] == {"A": 1, "B": 0}

async def test_giving_up_after_the_first_map_hands_the_series_to_the_other(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _ban_all(factory, clock, duel_id)
    await _play(factory, clock, duel_id, "A")
    duel = await _view(factory, duel_id, 1)
    loser = _player(duel, "A")
    done = await series.give_up(factory, duel_id, loser, clock.op(), now=clock.tick())
    assert (done["state"], done["outcome"], done["winner"]) == (states.COMPLETED, "surrender", _player(duel, "B"))

async def test_giving_up_before_any_map_cancels_without_a_winner(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    done = await series.give_up(factory, duel_id, 1, clock.op(), now=clock.tick())
    assert (done["state"], done["winner"]) == (states.CANCELLED, None)

async def test_a_pool_ack_must_name_the_shown_pool(factory):
    clock = Clock()
    invited = await service.invite(factory, 1, 2, clock.op(), now=clock.now)
    await service.accept(factory, invited["id"], 2, clock.op(), now=clock.tick())
    await series.pool_built(factory, invited["id"], _pool(), "c", "f", clock.tick())
    await series.room_ready(factory, invited["id"], clock.tick())
    await series.players_present(factory, invited["id"], clock.tick())
    with pytest.raises(DuelError) as wrong:
        await series.pool_ack(factory, invited["id"], 1, "0" * 64, clock.op(), now=clock.tick())
    assert wrong.value.code == "MAP_REVISION_CHANGED"
    assert await deadlines.sweep(factory, clock.tick(16)) == 1
    cancelled = await _view(factory, invited["id"], 1)
    assert (cancelled["state"], cancelled["outcome"]) == (states.CANCELLED, "pool_not_delivered")

async def test_no_pool_cancels_and_a_far_pool_offers_a_range(factory):
    clock = Clock()
    first = await service.invite(factory, 1, 2, clock.op(), now=clock.now)
    await service.accept(factory, first["id"], 2, clock.op(), now=clock.tick())
    gone = await series.pool_built(factory, first["id"], generator.Pool("unavailable", deficits=["HR2"]), "c", "f", clock.tick())
    assert (gone["state"], gone["outcome"]) == (states.CANCELLED, "pool_unavailable")
    second = await service.invite(factory, 1, 2, clock.op(), now=clock.tick())
    await service.accept(factory, second["id"], 2, clock.op(), now=clock.tick())
    offered = await series.pool_built(factory, second["id"], generator.Pool("range_offered", 4, offer={"NM1": (6.0, 6.8)}), "c", "f",
                                      clock.tick())
    assert offered["state"] == states.RANGE_OFFERED
    assert await deadlines.sweep(factory, clock.tick(61)) == 1
    assert (await _view(factory, second["id"], 1))["outcome"] == "range_declined"
