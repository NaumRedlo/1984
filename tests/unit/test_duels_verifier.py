from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel import Duel, DuelAttempt, DuelEvidence, DuelRound
from services.duels import verifier
from services.duels.verifier import Budget, Expected, judge, next_check

STARTED = datetime(2026, 10, 23, 12, 0, 0)

def _game(**over):
    game = {
        "id": 555, "beatmap_id": 658127, "mode": "osu", "mode_int": 0, "scoring_type": "scorev2", "team_type": "head-to-head",
        "start_time": "2026-10-23T12:00:05Z", "end_time": "2026-10-23T12:04:00Z", "mods": ["NF"],
        "scores": [{"user_id": 2, "score": 812440, "mods": []}, {"user_id": 3, "score": 790115, "mods": []}],
    }
    game.update(over)
    return game

def _expected(**over):
    values = dict(beatmap_id=658127, players=frozenset({2, 3}), mods=frozenset({"NF"}), tiebreak=False, started_after=STARTED)
    values.update(over)
    return Expected(**values)

def test_a_proper_game_names_the_higher_score_v2():
    verdict = judge(_game(), _expected())
    assert (verdict.state, verdict.winner, verdict.scores) == ("valid", 2, {2: 812440, 3: 790115})
    assert verdict.mods == {2: ["NF"], 3: ["NF"]}

def test_equal_scores_are_a_tie_and_zero_is_a_real_result():
    verdict = judge(_game(scores=[{"user_id": 2, "score": 0, "mods": []}, {"user_id": 3, "score": 0, "mods": []}]), _expected())
    assert (verdict.state, verdict.winner) == ("tie", None)

@pytest.mark.parametrize("change, reason", [
    ({"scoring_type": "score"}, "score_mode"),
    ({"team_type": "team-vs"}, "team_mode"),
    ({"beatmap_id": 1}, "beatmap"),
    ({"mode": "taiko", "mode_int": 1}, "mode"),
    ({"start_time": "2026-10-23T11:59:00Z"}, "started_before_command"),
])
def test_a_game_in_the_wrong_conditions_never_counts(change, reason):
    verdict = judge(_game(**change), _expected())
    assert verdict.state == "invalid" and reason in verdict.reasons and verdict.winner is None

def test_an_unfinished_game_waits_and_a_missing_or_extra_player_does_not_count():
    assert judge(_game(end_time=None), _expected()).state == "pending"
    alone = judge(_game(scores=[{"user_id": 2, "score": 5, "mods": []}]), _expected())
    assert (alone.state, alone.reasons) == ("incomplete", ["missing_player"])
    crowd = judge(_game(scores=[{"user_id": 2, "score": 5}, {"user_id": 3, "score": 4}, {"user_id": 9, "score": 1}]), _expected())
    assert crowd.reasons == ["stranger"]

def test_every_player_needs_no_fail_and_exactly_the_slot_mods():
    without = judge(_game(mods=[], scores=[{"user_id": 2, "score": 9, "mods": ["NF"]}, {"user_id": 3, "score": 8, "mods": []}]), _expected())
    assert (without.state, without.reasons) == ("invalid", ["no_fail:3"])
    extra = judge(_game(scores=[{"user_id": 2, "score": 9, "mods": ["HD"]}, {"user_id": 3, "score": 8, "mods": []}]), _expected())
    assert extra.reasons == ["mods:2"]
    hidden = judge(_game(mods=["NF", "HD"]), _expected(mods=frozenset({"NF", "HD"})))
    assert hidden.state == "valid"
    marked = judge(_game(mods=[{"acronym": "NF"}, {"acronym": "DT"}]), _expected(mods=frozenset({"NF", "DT"})))
    assert marked.state == "valid"

def test_the_tiebreaker_lets_each_choose_hidden_or_hard_rock_but_nothing_else():
    free = _game(scores=[{"user_id": 2, "score": 9, "mods": ["HD", "HR"]}, {"user_id": 3, "score": 8, "mods": []}])
    assert judge(free, _expected(tiebreak=True)).state == "valid"
    fast = _game(scores=[{"user_id": 2, "score": 9, "mods": ["DT"]}, {"user_id": 3, "score": 8, "mods": ["HD"]}])
    assert judge(fast, _expected(tiebreak=True)).reasons == ["mods:2"]

def test_checks_follow_five_fifteen_thirty_then_every_minute_up_to_ten():
    assert [next_check(t) for t in (0, 5, 14, 15, 29, 30, 61, 599, 600)] == [5, 15, 15, 30, 30, 60, 120, 600, None]

async def test_the_budget_lets_thirty_requests_a_minute_through():
    moments = [0.0]
    budget = Budget(per_minute=3, clock=lambda: moments[0])
    for _ in range(3):
        await budget.take()
    assert len(budget.sent) == 3
    moments[0] = 61.0
    await budget.take()
    assert list(budget.sent) == [61.0]

class FakeOsu:
    def __init__(self, events):
        self.events = events
        self.asked = []

    async def get_match(self, match_id, after=None, limit=None):
        self.asked.append(after)
        newer = [event for event in self.events if after is None or event["id"] > after]
        return {"events": newer[:limit]}

async def test_history_reads_every_page_after_the_cursor():
    events = [{"id": number, "detail": {"type": "other"}} for number in range(1, 251)]
    osu = FakeOsu(events)
    found, cursor = await verifier.history(osu, 7, None, Budget(per_minute=1000))
    assert len(found) == 250 and cursor == 250 and osu.asked == [None, 100, 200]
    again, same = await verifier.history(osu, 7, 250, Budget(per_minute=1000))
    assert again == [] and same == 250

def test_the_latest_version_of_each_game_wins():
    events = [{"id": 1, "game": {"id": 5, "end_time": None}}, {"id": 2, "game": {"id": 5, "end_time": "x"}}, {"id": 3}]
    assert verifier.games(events) == {5: {"id": 5, "end_time": "x"}}

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'verify.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add(Duel(id="duel-1", mode="direct", state="VERIFYING", rules_version="duels-v1", seed="s"))
        await session.flush()
        session.add(DuelRound(id=1, duel_id="duel-1", ordinal=1, slot="NM1", state="playing"))
        await session.flush()
        session.add_all([DuelAttempt(id=1, round_id=1, attempt_no=1, expected_mods=["NF"]),
                         DuelAttempt(id=2, round_id=1, attempt_no=2, expected_mods=["NF"])])
        await session.commit()
    yield made
    await engine.dispose()

async def test_evidence_is_kept_once_per_payload_and_a_changed_payload_is_a_new_version(factory):
    async with factory() as session:
        first = await verifier.keep(session, 7, {"id": 1, "game": {"id": 5, "end_time": None}})
        same = await verifier.keep(session, 7, {"game": {"end_time": None, "id": 5}, "id": 1})
        changed = await verifier.keep(session, 7, {"id": 1, "game": {"id": 5, "end_time": "x"}})
        await session.commit()
        assert first.id == same.id and changed.id != first.id
        assert await session.scalar(select(func.count()).select_from(DuelEvidence)) == 2

async def test_one_game_belongs_to_one_attempt_only(factory):
    async with factory() as session:
        assert await verifier.bind(session, 1, 555)
        assert await verifier.bind(session, 1, 555)
        assert not await verifier.bind(session, 2, 555)
        assert not await verifier.bind(session, 1, 556)
        await session.commit()
        assert (await session.get(DuelAttempt, 1)).game_id == 555
