import asyncio
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.player import Player
from db.models.user import User
from services.refresh.orchestrator import claim_refresh, refresh_user
from services.refresh.prepared import PreparedProfile
from services.refresh.runtime import background_request
from tasks.profile_updater import ProfileUpdater
from utils.osu.api_client import OsuApiClient
from utils.timeutils import utcnow


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch):
    import tasks.profile_updater as module
    import services.oauth.token_manager as tokens
    import utils.title_progress as titles
    from services.render_farm import invites

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(module, "AsyncSessionFactory", factory)
    monkeypatch.setattr(tokens, "token_of", AsyncMock(return_value=None))
    monkeypatch.setattr(titles, "refresh_user_titles", AsyncMock(return_value=[]))
    monkeypatch.setattr(invites, "linked_players", lambda: set())
    monkeypatch.setattr(invites, "present", lambda: [])
    yield factory
    await engine.dispose()


async def add_player(factory, number=1, *, seen=True):
    async with factory() as session:
        user = User(chat_id=-number, telegram_id=number, osu_user_id=number, osu_username=f"p{number}",
                    last_seen_at=utcnow() if seen else None)
        session.add(user)
        await session.commit()
        return user.player_id


def api_for(number=1):
    return SimpleNamespace(
        get_user_data=AsyncMock(return_value={"id": number, "username": f"p{number}", "pp": 4567,
                                            "avatar_url": "avatar-new", "cover_url": "cover-new"}),
        get_user_best_scores=AsyncMock(return_value=[]),
        effective_sr=AsyncMock(return_value=5.4),
        ranked_dates=AsyncMock(return_value={}),
        _download_image_bytes=AsyncMock(return_value=b"image"),
    )


async def test_best_score_failure_never_marks_full_refresh_complete(database):
    pid = await add_player(database)
    async with database() as session:
        player = await session.get(Player, pid)
        api = SimpleNamespace(sync_user_stats_from_api=AsyncMock(return_value=True),
                              sync_user_best_scores=AsyncMock(return_value=False))
        assert not await refresh_user(player, session, api, oauth_token="")
        assert player.last_full_update is None


@pytest.mark.parametrize("response", [None, {}, {"error": "upstream"}])
async def test_best_score_endpoint_does_not_disguise_errors_as_empty(response):
    api = OsuApiClient()
    api._make_request = AsyncMock(return_value=response)
    with pytest.raises(ValueError):
        await api.get_user_best_scores(1)
    assert api._make_request.call_args.kwargs["strict"] is True


async def test_empty_best_scores_are_a_successful_full_refresh(database):
    pid = await add_player(database)
    api = api_for()
    updater = ProfileUpdater(api)
    assert await updater.run_job(pid, "background_full") == "updated"
    async with database() as session:
        player = await session.get(Player, pid)
        assert player.last_full_update is not None
        assert player.best_scores_baseline_at is not None
    api.get_user_best_scores.assert_awaited_once()


async def test_same_osu_account_shares_reservation_and_cancellation_releases_it():
    one = SimpleNamespace(id=1, osu_user_id=10)
    two = SimpleNamespace(id=2, osu_user_id=10)
    entered = asyncio.Event()

    async def holding():
        async with claim_refresh(one) as acquired:
            assert acquired
            entered.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(holding())
    await entered.wait()
    async with claim_refresh(two) as acquired:
        assert not acquired
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with claim_refresh(two) as acquired:
        assert acquired


async def test_one_job_updates_all_chat_rows_and_preserves_preferences(database):
    pid = await add_player(database)
    async with database() as session:
        session.add(User(chat_id=-2, telegram_id=1, osu_user_id=1, osu_username="p1", render_fps=120))
        await session.commit()
    api = api_for()
    updater = ProfileUpdater(api)
    assert await updater._due("stats_only", 150) == [pid]
    assert await updater.run_job(pid, "stats_only") == "updated"
    async with database() as session:
        users = (await session.scalars(select(User).order_by(User.id))).all()
        assert [u.player_pp for u in users] == [4567, 4567]
        assert all(u.last_api_update is not None for u in users)
        assert users[1].render_fps == 120
    api.get_user_data.assert_awaited_once()
    api._download_image_bytes.assert_not_awaited()


async def test_no_database_session_is_held_during_network_calls(database, monkeypatch):
    import tasks.profile_updater as module
    pid = await add_player(database)
    opened = 0

    @asynccontextmanager
    async def tracked():
        nonlocal opened
        async with database() as session:
            opened += 1
            try:
                yield session
            finally:
                opened -= 1
    monkeypatch.setattr(module, "AsyncSessionFactory", tracked)
    api = api_for()
    for name in vars(api):
        method = getattr(api, name)
        result = method.return_value
        async def checked(*args, _result=result, **kwargs):
            assert opened == 0
            return _result
        method.side_effect = checked
    assert await ProfileUpdater(api).run_job(pid, "background_full") == "updated"
    api._download_image_bytes.assert_awaited()


async def test_a_newer_profile_wins_over_a_slow_background_response(database):
    pid = await add_player(database)
    api = api_for()

    async def newer(*args, **kwargs):
        async with database() as session:
            player = await session.get(Player, pid)
            player.player_pp = 9000
            player.last_api_update = utcnow()
            await session.commit()
        return {"id": 1, "username": "old", "pp": 1}
    api.get_user_data.side_effect = newer
    assert await ProfileUpdater(api).run_job(pid, "stats_only") == "superseded"
    async with database() as session:
        assert (await session.get(Player, pid)).player_pp == 9000


async def test_failure_backoff_excludes_oldest_so_other_players_can_run(database):
    first = await add_player(database)
    second = await add_player(database, 2)
    api = api_for()
    api.get_user_data.side_effect = RuntimeError("unavailable")
    updater = ProfileUpdater(api)
    assert await updater.run_job(first, "stats_only") == "failed"
    failures, until = updater.retries[(first, "stats_only")]
    assert failures == 1 and until > time.monotonic()
    assert await updater._due("stats_only", 1) == [second]
    api.get_user_data.side_effect = None
    updater.retries[(first, "stats_only")] = (1, 0)
    assert await updater.run_job(first, "stats_only") == "updated"
    assert (first, "stats_only") not in updater.retries


async def test_deadline_rolls_back_and_releases_reservation(database):
    pid = await add_player(database)
    api = api_for()
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()
    api.get_user_data.side_effect = hang
    updater = ProfileUpdater(api)
    updater.PROFILE_TIMEOUT = .02
    assert await updater.run_job(pid, "stats_only") == "timeout"
    async with database() as session:
        player = await session.get(Player, pid)
        assert player.last_api_update is None
        async with claim_refresh(player) as acquired:
            assert acquired
    assert not updater.active
    assert updater.health()["outcomes"]["timeout"] == 1
    assert not background_request.get()


async def test_queue_is_bounded_and_rescheduling_does_not_duplicate(database):
    for i in range(1, 5):
        await add_player(database, i)
    updater = ProfileUpdater(None)
    updater.queues = {mode: asyncio.Queue(maxsize=2) for mode in updater.queues}
    await updater.schedule()
    await updater.schedule()
    assert len(updater.pending) == 4
    assert all(queue.qsize() == 2 for queue in updater.queues.values())


async def test_slow_full_update_does_not_block_stats_worker():
    updater = ProfileUpdater(None)
    full_started = asyncio.Event()
    stats_done = asyncio.Event()
    async def work(pid, mode):
        if mode == "background_full":
            full_started.set()
            await asyncio.Event().wait()
        stats_done.set()
    updater.run_job = work
    full = asyncio.create_task(updater._worker("background_full"))
    stats = asyncio.create_task(updater._worker("stats_only"))
    try:
        updater.queues["background_full"].put_nowait(1)
        await full_started.wait()
        updater.queues["stats_only"].put_nowait(2)
        await asyncio.wait_for(stats_done.wait(), 1)
        assert not full.done()
    finally:
        full.cancel()
        stats.cancel()
        await asyncio.gather(full, stats, return_exceptions=True)


async def test_active_and_inactive_players_have_different_cadence(database):
    active = await add_player(database, 1)
    inactive = await add_player(database, 2, seen=False)
    async with database() as session:
        for player in (await session.scalars(select(Player))).all():
            player.last_api_update = utcnow() - timedelta(minutes=10)
            player.last_full_update = utcnow() - timedelta(hours=3)
        await session.commit()
    updater = ProfileUpdater(None)
    assert await updater._due("stats_only", 150) == [active]
    assert await updater._due("background_full", 150) == [active]


async def test_full_refresh_reuses_fresh_stats_and_loads_missing_images(database):
    pid = await add_player(database)
    api = api_for()
    updater = ProfileUpdater(api)
    assert await updater.run_job(pid, "stats_only") == "updated"
    assert await updater.run_job(pid, "background_full") == "updated"
    api.get_user_data.assert_awaited_once()
    async with database() as session:
        user = await session.scalar(select(User))
        assert user.avatar_data == b"image"
        assert user.cover_data == b"image"


async def test_command_timestamp_is_mirrored_to_player_and_other_chats(database):
    pid = await add_player(database)
    async with database() as session:
        session.add(User(chat_id=-2, telegram_id=1, osu_user_id=1, osu_username="p1"))
        await session.commit()
    async with database() as session:
        user = await session.scalar(select(User).order_by(User.id))
        user.last_api_update = utcnow()
        user.last_full_update = utcnow()
        await session.commit()
    async with database() as session:
        users = (await session.scalars(select(User))).all()
        player = await session.get(Player, pid)
        assert all(u.last_api_update == player.last_api_update for u in users)
        assert all(u.last_full_update == player.last_full_update for u in users)


async def test_foreground_requests_overtake_queued_background_requests():
    api = OsuApiClient()
    api.RATE_LIMIT_DELAY = 0
    await api._request_lock.acquire()
    order = []
    async def request(name, background):
        token = background_request.set(background)
        try:
            await api._rate_limit()
            order.append(name)
        finally:
            background_request.reset(token)
    bg = asyncio.create_task(request("background", True))
    await asyncio.sleep(0)
    fg = asyncio.create_task(request("command", False))
    await asyncio.sleep(0)
    api._request_lock.release()
    await asyncio.wait_for(asyncio.gather(bg, fg), 1)
    assert order == ["command", "background"]
    assert api._foreground_waiters == 0


async def test_present_dossier_player_gets_active_cadence(database, monkeypatch):
    from services.render_farm import invites
    pid = await add_player(database, seen=False)
    async with database() as session:
        player = await session.get(Player, pid)
        player.last_api_update = utcnow() - timedelta(minutes=10)
        await session.commit()
    monkeypatch.setattr(invites, "present", lambda: [invites.Owner(1, "p1", pid)])
    updater = ProfileUpdater(api_for())
    assert await updater._due("stats_only", 10) == [pid]
    assert await updater.run_job(pid, "stats_only") == "updated"


async def test_optional_enrichment_failure_does_not_lose_scores(database):
    from db.models.best_score import UserBestScore
    pid = await add_player(database)
    api = api_for()
    api.get_user_best_scores.return_value = [{"id": 123, "pp": 100,
                                            "beatmap": {"id": 12, "difficulty_rating": 5},
                                            "beatmapset": {"id": 34}}]
    api.effective_sr.side_effect = RuntimeError("assay unavailable")
    api.ranked_dates.side_effect = RuntimeError("map service unavailable")
    api._download_image_bytes.side_effect = RuntimeError("image unavailable")
    assert await ProfileUpdater(api).run_job(pid, "background_full") == "updated"
    async with database() as session:
        score = await session.scalar(select(UserBestScore))
        assert score.score_id == 123 and score.star_rating == 5
        assert score.eff_sr is None


@pytest.mark.parametrize("scores, outcome, remaining", [([], "updated", 0), (None, "failed", 1), ([{}], "failed", 1)])
async def test_only_valid_empty_response_removes_old_top_scores(database, scores, outcome, remaining):
    from db.models.best_score import UserBestScore
    pid = await add_player(database)
    async with database() as session:
        session.add(UserBestScore(player_id=pid, score_id=123, beatmap_id=12, pp=100))
        await session.commit()
    api = api_for()
    api.get_user_best_scores.return_value = scores
    assert await ProfileUpdater(api).run_job(pid, "background_full") == outcome
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(UserBestScore)) == remaining
        if outcome == "failed":
            assert (await session.get(Player, pid)).last_full_update is None


async def test_shutdown_cancels_workers_and_releases_inflight_job(database, monkeypatch):
    import tasks.profile_updater as module
    pid = await add_player(database)
    api = api_for()
    entered = asyncio.Event()
    async def hanging(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    api.get_user_data.side_effect = hanging
    monkeypatch.setattr(module, "ensure_period_snapshot", AsyncMock())
    updater = ProfileUpdater(api)
    shutdown = asyncio.Event()
    task = asyncio.create_task(updater.start_loop(shutdown))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        shutdown.set()
        await asyncio.wait_for(task, 1)
        assert not updater.active
        assert not updater.pending
        async with database() as session:
            player = await session.get(Player, pid)
            async with claim_refresh(player) as acquired:
                assert acquired
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_worker_is_restarted_after_unexpected_exit(database, monkeypatch):
    import tasks.profile_updater as module
    monkeypatch.setattr(module, "ensure_period_snapshot", AsyncMock())
    updater = ProfileUpdater(None)
    updater.TICK_SECONDS = .01
    original = updater._worker
    starts = 0
    recovered = asyncio.Event()
    async def worker(mode):
        nonlocal starts
        if mode == "background_full":
            starts += 1
            if starts == 1:
                raise RuntimeError("worker failed")
            recovered.set()
        await original(mode)
    updater._worker = worker
    shutdown = asyncio.Event()
    task = asyncio.create_task(updater.start_loop(shutdown))
    try:
        await asyncio.wait_for(recovered.wait(), 1)
        assert starts == 2
        assert updater.health()["last_schedule"] is not None
    finally:
        shutdown.set()
        await asyncio.wait_for(task, 1)


async def test_cancelling_a_waiting_command_unblocks_background():
    api = OsuApiClient()
    api.RATE_LIMIT_DELAY = 0
    await api._request_lock.acquire()
    command = asyncio.create_task(api._rate_limit())
    await asyncio.sleep(0)
    command.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command
    api._request_lock.release()
    assert api._foreground_waiters == 0
    marker = background_request.set(True)
    try:
        await asyncio.wait_for(api._rate_limit(), 1)
    finally:
        background_request.reset(marker)


def test_health_exposes_queue_age_and_recent_update_duration():
    updater = ProfileUpdater(None)
    updater.enqueued_at[(1, "stats_only")] = time.monotonic() - 60
    updater.durations.extend([1, 2, 3])
    health = updater.health()
    assert health["oldest_queued_seconds"] >= 59
    assert health["job_p95_seconds"] == 3
