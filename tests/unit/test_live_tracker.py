from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.user import User
from db.models.player import Player
from db.models.witnessed_play import WitnessedPlay
from db.models.map_attempt import UserMapAttempt
from sqlalchemy import select
from tasks import live_tracker
from tasks.live_tracker import EACH_SECONDS, LiveTracker, ROUND_MOST
from services.render_farm import invites
from services import recent_plays


@pytest.fixture(autouse=True)
def fresh_presence(monkeypatch):
    monkeypatch.setattr(invites, "_seen", {})
    monkeypatch.setattr(invites, "_owners", {})
    monkeypatch.setattr(invites, "_good", set())


class _Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class _Client:
    def __init__(self, scores):
        self.scores = scores
        self.asked = []
        self.synced = []

    async def get_user_recent_scores(self, osu_user_id, limit=1, oauth_token=None, mode=None):
        self.asked.append((osu_user_id, limit, mode))
        return self.scores

    async def sync_user_map_attempts(self, user, session, scores):
        self.synced.append(user.chat_id)
        return len(scores)


@pytest_asyncio.fixture
async def database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    import db.database

    monkeypatch.setattr(db.database, "AsyncSessionFactory", factory)
    yield factory
    await engine.dispose()


def test_the_longest_unasked_come_first_and_a_nudge_jumps_the_queue():
    clock = _Clock(1000.0)
    tracker = LiveTracker(_Client([]), clock=clock)
    tracker.asked = {1: 1000.0 - EACH_SECONDS - 5, 2: 990.0}
    assert tracker.due([1, 2, 3]) == [3, 1]
    tracker.nudge(2)
    assert tracker.due([1, 2, 3])[:1] != [2], "a nudge waits a moment for osu! to take the score"
    clock.now += 6
    assert tracker.due([1, 2, 3])[0] == 2
    clock.now += 30
    assert tracker.due([1, 2, 3])[0] == 2, "and it is asked once more a little later"
    clock.now += 60
    assert 2 not in tracker.due([2]), "then it goes back to its turn"


def test_a_round_is_bounded():
    tracker = LiveTracker(_Client([]), clock=_Clock())
    assert len(tracker.due(list(range(100)))) == ROUND_MOST


async def test_a_player_s_recent_plays_are_kept_once_however_many_groups_they_are_in(database, monkeypatch):
    async def quietly(user, plays, session):
        return []

    import utils.title_progress

    monkeypatch.setattr(utils.title_progress, "evaluate_recent_plays", quietly)
    async with database() as session:
        session.add_all([
            User(chat_id=-1, telegram_id=7, osu_username="NaumRedlo", osu_user_id=70),
            User(chat_id=-2, telegram_id=7, osu_username="NaumRedlo", osu_user_id=70),
            User(chat_id=-1, telegram_id=8, osu_username="kotofey", osu_user_id=80),
        ])
        await session.commit()
    client = _Client([{"id": 1, "beatmap": {}, "statistics": {}, "passed": True}])
    tracker = LiveTracker(client, clock=_Clock())
    assert sorted(await tracker.known()) == [70, 80]
    assert await tracker.catch(70) == 1
    assert len(client.synced) == 1
    assert client.asked == [(70, 20, "osu")]
    assert 70 in tracker.asked


def test_a_nudge_without_a_running_tracker_is_not_heard():
    live_tracker.set_current(None)
    assert live_tracker.nudge(70) is False


def test_presence_is_per_device_expires_and_revocation_is_immediate():
    owner = invites.Owner(7, "Naum")
    invites.remember("mac", owner)
    invites.remember("windows", owner)
    invites.remember("worker")
    invites.touch("mac", now=100, keep=invites.PRESENCE_BEAT_FOR)
    invites.touch("windows", now=110, keep=invites.PRESENCE_BEAT_FOR)
    invites.touch("worker", now=120)
    invites.touch("stranger", now=120)
    assert invites.present(now=129) == [owner, owner]
    assert invites.present(now=130) == [owner]
    invites.forget(invites.digest("windows"))
    assert invites.present(now=131) == []


def test_old_clients_get_a_longer_lease_and_regular_requests_keep_the_short_lease():
    owner = invites.Owner(7, "Naum")
    invites.remember("old", owner)
    invites.touch("old", now=100)
    assert invites.present(now=279) == [owner]
    assert invites.present(now=280) == []
    invites.touch("old", now=300, keep=invites.PRESENCE_BEAT_FOR)
    invites.touch("old", now=310)
    assert invites.present(now=339) == [owner]
    assert invites.present(now=340) == []


async def test_an_online_player_is_skipped_even_when_nudged_and_returns_when_offline(database, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(invites, "monotonic", clock)
    async with database() as session:
        session.add_all([
            User(chat_id=-1, telegram_id=7, osu_username="Naum", osu_user_id=70),
            Player(osu_user_id=80, osu_username="alone"),
        ])
        await session.commit()
        alone = (await session.execute(select(Player).where(Player.osu_user_id == 80))).scalar_one()
        invites.remember("mac", invites.Owner(7, "Naum"))
        invites.remember("alone", invites.Owner(0, "alone", alone.id))
    client = _Client([])
    tracker = LiveTracker(client, clock=clock)
    assert sorted(await tracker.known()) == [70, 80]
    tracker.asked[70] = clock.now
    invites.touch("mac", keep=invites.PRESENCE_BEAT_FOR)
    invites.touch("alone", keep=invites.PRESENCE_BEAT_FOR)
    tracker.nudge(70)
    clock.now += 6
    assert await tracker.known() == []
    assert tracker.due([]) == []
    assert tracker.nudged == {}
    assert await tracker.catch(70) == 0
    assert client.asked == []
    clock.now += 24
    assert sorted(await tracker.known()) == [70, 80]
    assert 70 in tracker.due(await tracker.known())
    await tracker.catch(70)
    assert client.asked == [(70, 20, "osu")]


async def test_a_player_coming_online_during_the_api_request_is_not_written(database):
    async with database() as session:
        session.add(User(chat_id=-1, telegram_id=7, osu_username="Naum", osu_user_id=70))
        await session.commit()
    invites.remember("mac", invites.Owner(7, "Naum"))
    client = _Client([])
    async def recent(*args, **kwargs):
        invites.touch("mac", keep=invites.PRESENCE_BEAT_FOR)
        return [{"id": 1, "beatmap": {}, "statistics": {}, "passed": True}]
    client.get_user_recent_scores = recent
    tracker = LiveTracker(client)
    assert await tracker.catch(70) == 0
    assert client.synced == []


async def test_a_player_coming_online_during_a_write_rolls_back_the_attempt(database, monkeypatch):
    import utils.title_progress
    monkeypatch.setattr(utils.title_progress, "evaluate_recent_plays", AsyncMock(return_value=[]))
    async with database() as session:
        session.add(User(chat_id=-1, telegram_id=7, osu_username="Naum", osu_user_id=70))
        await session.commit()
    invites.remember("mac", invites.Owner(7, "Naum"))
    client = _Client([{"id": 1, "beatmap": {}, "statistics": {}, "passed": True}])
    async def sync(user, session, scores):
        session.add(UserMapAttempt(player_id=user.player_id, score_id=1, beatmap_id=100, pp=10))
        await session.flush()
        invites.touch("mac", keep=invites.PRESENCE_BEAT_FOR)
        return 1
    client.sync_user_map_attempts = sync
    assert await LiveTracker(client).catch(70) == 0
    async with database() as session:
        assert (await session.execute(select(UserMapAttempt))).scalars().all() == []


async def test_offline_fallback_does_not_copy_witness_results_but_keeps_other_plays(database, monkeypatch):
    import utils.title_progress
    evaluate = AsyncMock(return_value=[])
    monkeypatch.setattr(utils.title_progress, "evaluate_recent_plays", evaluate)
    at = datetime(2026, 10, 5, 12)
    async with database() as session:
        user = User(chat_id=-1, telegram_id=7, osu_username="Naum", osu_user_id=70)
        session.add(user)
        await session.flush()
        session.add(WitnessedPlay(player_id=user.player_id, replay_hash="a" * 32, beatmap_md5="b" * 32,
            beatmap_id=100, played_at=at, score=100000, max_combo=500,
            count_100=2, count_50=0, count_miss=0, passed=True, mods="HD"))
        await session.commit()
    raw = {"id": 1, "beatmap": {"id": 100}, "created_at": at.isoformat(), "score": 100000,
           "statistics": {"count_100": 2, "count_50": 0, "count_miss": 0}, "max_combo": 500,
           "passed": True, "mods": ["HD", "CL"]}
    others = [dict(raw, id=2, mods=["HR"]), dict(raw, id=3, passed=False),
              dict(raw, id=4, beatmap={"id": 101}), dict(raw, id=5, created_at=(at + timedelta(hours=1)).isoformat()),
              dict(raw, id=6, score=99999, max_combo=499), dict(raw, id=7, created_at=None),
              dict(raw, id=8, created_at=(at + timedelta(minutes=2)).isoformat())]
    client = _Client([raw] + others)
    tracker = LiveTracker(client)
    assert await tracker.catch(70) == len(others)
    assert len(evaluate.await_args.args[1]) == len(others)
    async with database() as session:
        assert await recent_plays.unseen(session, user.player_id, [raw] + others) == others
    client.scores = [raw]
    client.synced.clear()
    evaluate.reset_mock()
    assert await tracker.catch(70) == 0
    assert client.synced == []
    evaluate.assert_not_awaited()
