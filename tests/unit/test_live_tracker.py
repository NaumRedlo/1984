import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models  # noqa: F401
from db.database import Base
from db.models.user import User
from tasks import live_tracker
from tasks.live_tracker import EACH_SECONDS, LiveTracker, ROUND_MOST


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


async def test_a_player_s_recent_plays_reach_every_group_they_are_in(database, monkeypatch):
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
    assert await tracker.catch(70) == 2
    assert sorted(client.synced) == [-2, -1]
    assert client.asked == [(70, 20, "osu")]
    assert 70 in tracker.asked


def test_a_nudge_without_a_running_tracker_is_not_heard():
    live_tracker.set_current(None)
    assert live_tracker.nudge(70) is False
