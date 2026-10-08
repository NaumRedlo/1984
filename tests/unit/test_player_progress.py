from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db import player_sync
from db.database import Base
from db.migrations import add_players, move_progress_to_players
from db.migrations.add_players import run_players_migration
from db.migrations.move_progress_to_players import merged, run_player_progress_migration
from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.models.player import Player
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from db.player_data import clear_if_last
from utils.title_progress import calc_title_rarity, refresh_user_titles

HERE = -100
THERE = -200

LEGACY = (
    "CREATE TABLE user_best_scores (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, score_id BIGINT NOT NULL, beatmap_id INTEGER NOT NULL,"
    " pp FLOAT NOT NULL, title VARCHAR(255), previous_pp FLOAT, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    " CONSTRAINT uq_user_best_scores_user_score UNIQUE (user_id, score_id))",
    "CREATE INDEX ix_user_best_scores_user_pp ON user_best_scores (user_id, pp)",
    "CREATE TABLE user_map_attempts (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, score_id BIGINT NOT NULL, beatmap_id INTEGER NOT NULL,"
    " pp FLOAT NOT NULL, passed BOOLEAN, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "CREATE UNIQUE INDEX legacy_attempt_score ON user_map_attempts (user_id, score_id)",
    "CREATE TABLE user_title_progress (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title_code VARCHAR(50) NOT NULL,"
    " current_value INTEGER NOT NULL, unlocked BOOLEAN NOT NULL, unlocked_at DATETIME, CONSTRAINT uq_user_title UNIQUE (user_id, title_code))",
)

@pytest.fixture(autouse=True)
def sync_as_it_was():
    yield
    player_sync.switch_on()

@pytest_asyncio.fixture
async def old(tmp_path, monkeypatch):
    monkeypatch.setattr(add_players, "REPORT", str(tmp_path / "players.log"))
    monkeypatch.setattr(move_progress_to_players, "REPORT", str(tmp_path / "progress.log"))
    player_sync.switch_on(False)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table in ("user_best_scores", "user_map_attempts", "user_title_progress"):
            await conn.execute(text(f"DROP TABLE {table}"))
        for line in LEGACY:
            await conn.execute(text(line))
    yield engine
    await engine.dispose()

async def _legacy_rows(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([
            User(id=1, chat_id=HERE, telegram_id=7, osu_username="alice", osu_user_id=1001, last_api_update=datetime(2026, 9, 1),
                 level=90, grade_count_ss=3, profile_opens_best=2, compare_uses=10, active_day=date(2026, 9, 1), active_streak=4,
                 active_streak_best=4, best_scores_baseline_at=datetime(2026, 5, 1), week_plays_best=120),
            User(id=2, chat_id=THERE, telegram_id=7, osu_username="alice", osu_user_id=1001, last_api_update=datetime(2026, 9, 20),
                 level=95, grade_count_ss=2, profile_opens_best=6, compare_uses=5, active_day=date(2026, 9, 20), active_streak=1,
                 active_streak_best=9, best_scores_baseline_at=datetime(2026, 7, 1), comeback_done=True),
            User(id=3, chat_id=HERE, telegram_id=8, osu_username="bob", osu_user_id=1002, last_api_update=datetime(2026, 9, 5)),
            User(id=4, chat_id=HERE, telegram_id=9, osu_username="nobody", osu_user_id=None),
        ])
        await s.commit()
    async with engine.begin() as conn:
        for row in (
            "(1, 1, 500, 9, 100.0, 'old pp', NULL)", "(2, 2, 500, 9, 120.0, 'fresh pp', 100.0)", "(3, 1, 501, 9, 90.0, 'only here', NULL)",
            "(4, 3, 500, 9, 80.0, 'bob', NULL)", "(5, 4, 777, 9, 1.0, 'no player', NULL)",
        ):
            await conn.execute(text(f"INSERT INTO user_best_scores (id, user_id, score_id, beatmap_id, pp, title, previous_pp) VALUES {row}"))
        for row in ("(1, 1, 900, 9, 10.0, 1)", "(2, 2, 900, 9, 10.0, 1)", "(3, 2, 901, 9, 0.0, 0)", "(4, 3, 900, 9, 5.0, 1)"):
            await conn.execute(text(f"INSERT INTO user_map_attempts (id, user_id, score_id, beatmap_id, pp, passed) VALUES {row}"))
        for row in (
            "(1, 'wysi', 1, 1, '2026-08-01 10:00:00')", "(2, 'wysi', 1, 1, '2026-06-01 10:00:00')", "(1, 'played_100k', 40000, 0, NULL)",
            "(2, 'played_100k', 52000, 0, NULL)", "(2, 'magic7', 1, 1, '2026-09-09 09:00:00')", "(3, 'wysi', 0, 0, NULL)",
        ):
            await conn.execute(text(f"INSERT INTO user_title_progress (user_id, title_code, current_value, unlocked, unlocked_at) VALUES {row}"))

@pytest.mark.sqlite_only
async def test_scores_attempts_and_titles_move_to_the_player_and_doubles_become_one(old):
    await _legacy_rows(old)
    await run_players_migration(old)
    said = await run_player_progress_migration(old)
    assert said["user_best_scores"] == {"before": 5, "after": 3, "merged": 1, "left": 1}
    assert said["user_map_attempts"] == {"before": 4, "after": 3, "merged": 1, "left": 0}
    assert said["user_title_progress"] == {"before": 6, "after": 4, "merged": 2, "left": 0}
    factory = async_sessionmaker(old, expire_on_commit=False)
    async with factory() as s:
        alice = (await s.execute(select(Player).where(Player.osu_user_id == 1001))).scalar_one()
        bob = (await s.execute(select(Player).where(Player.osu_user_id == 1002))).scalar_one()
        best = {(row.player_id, row.score_id): row for row in (await s.execute(select(UserBestScore))).scalars().all()}
        assert set(best) == {(alice.id, 500), (alice.id, 501), (bob.id, 500)}
        assert (best[(alice.id, 500)].pp, best[(alice.id, 500)].title, best[(alice.id, 500)].previous_pp) == (120.0, "fresh pp", 100.0)
        attempts = {(row.player_id, row.score_id) for row in (await s.execute(select(UserMapAttempt))).scalars().all()}
        assert attempts == {(alice.id, 900), (alice.id, 901), (bob.id, 900)}
        titles = {(row.player_id, row.title_code): row for row in (await s.execute(select(UserTitleProgress))).scalars().all()}
        wysi = titles[(alice.id, "wysi")]
        assert (wysi.unlocked, wysi.unlocked_at) == (True, datetime(2026, 6, 1, 10, 0))
        assert titles[(alice.id, "played_100k")].current_value == 52000 and not titles[(alice.id, "played_100k")].unlocked
        assert titles[(alice.id, "magic7")].unlocked and not titles[(bob.id, "wysi")].unlocked

@pytest.mark.sqlite_only
async def test_progress_of_two_chat_rows_becomes_one_on_the_player_and_on_both_rows(old):
    await _legacy_rows(old)
    await run_players_migration(old)
    await run_player_progress_migration(old)
    factory = async_sessionmaker(old, expire_on_commit=False)
    async with factory() as s:
        alice = (await s.execute(select(Player).where(Player.osu_user_id == 1001))).scalar_one()
        assert (alice.level, alice.grade_count_ss) == (95, 2)
        assert (alice.profile_opens_best, alice.active_streak_best, alice.week_plays_best, alice.compare_uses) == (6, 9, 120, 15)
        assert (alice.active_day, alice.active_streak, alice.comeback_done) == (date(2026, 9, 20), 1, True)
        assert alice.best_scores_baseline_at == datetime(2026, 5, 1)
        rows = (await s.execute(select(User).where(User.player_id == alice.id).order_by(User.id))).scalars().all()
        assert [(row.level, row.compare_uses, row.active_streak_best, row.active_day) for row in rows] == [(95, 15, 9, date(2026, 9, 20))] * 2

@pytest.mark.sqlite_only
async def test_the_move_happens_once_and_leaves_a_fresh_database_alone(old):
    await _legacy_rows(old)
    await run_players_migration(old)
    assert await run_player_progress_migration(old)
    assert await run_player_progress_migration(old) == {}
    fresh = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with fresh.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await run_players_migration(fresh)
        said = await run_player_progress_migration(fresh)
        assert said == {"players": 0, "columns": []}
        assert await run_player_progress_migration(fresh) == {}
    finally:
        await fresh.dispose()

@pytest.mark.sqlite_only
async def test_a_players_table_from_before_gains_the_new_columns(old):
    await _legacy_rows(old)
    await run_players_migration(old)
    async with old.begin() as conn:
        await conn.execute(text("ALTER TABLE players DROP COLUMN level"))
        await conn.execute(text("ALTER TABLE players DROP COLUMN active_streak_best"))
    said = await run_player_progress_migration(old)
    assert said["columns"] == ["level", "active_streak_best"]
    async with old.begin() as conn:
        assert (await conn.execute(text("SELECT level, active_streak_best FROM players WHERE osu_user_id = 1001"))).first() == (95, 9)

def test_merging_keeps_what_each_number_means():
    rows = [
        {"id": 1, "last_api_update": "2026-09-01", "level": 10, "compare_uses": None, "comeback_done": 0, "app_profile_at": None, "app_profile": None},
        {"id": 2, "last_api_update": None, "level": 99, "compare_uses": 3, "comeback_done": None, "app_profile_at": "2026-09-02", "app_profile": "{}"},
    ]
    out = merged(rows)
    assert (out["level"], out["compare_uses"], out["comeback_done"], out["app_profile"]) == (10, 3, 0, "{}")
    assert out["active_day"] is None and out["best_scores_baseline_at"] is None

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def test_a_second_chat_row_starts_with_what_the_player_already_earned(factory):
    async with factory() as s:
        s.add(User(chat_id=HERE, telegram_id=7, osu_username="alice", osu_user_id=1001, active_streak_best=30, compare_uses=12,
                   last_seen_at=datetime(2026, 9, 1), level=80))
        await s.commit()
    async with factory() as s:
        s.add(User(chat_id=THERE, telegram_id=7, osu_username="alice", osu_user_id=1001, last_seen_at=datetime(2026, 9, 9), level=81,
                   last_api_update=datetime(2026, 9, 9)))
        await s.commit()
    async with factory() as s:
        there = (await s.execute(select(User).where(User.chat_id == THERE))).scalar_one()
        here = (await s.execute(select(User).where(User.chat_id == HERE))).scalar_one()
        assert (there.active_streak_best, there.compare_uses) == (30, 12)
        assert there.last_seen_at == datetime(2026, 9, 9) and here.last_api_update is None
        there.compare_uses += 1
        await s.commit()
    async with factory() as s:
        here = (await s.execute(select(User).where(User.chat_id == HERE))).scalar_one()
        player = await s.get(Player, here.player_id)
        assert here.compare_uses == 13 and player.compare_uses == 13 and player.active_streak_best == 30

async def test_titles_are_counted_once_for_a_person_in_two_chats(factory):
    async with factory() as s:
        s.add_all([
            User(chat_id=HERE, telegram_id=7, osu_username="alice", osu_user_id=1001),
            User(chat_id=THERE, telegram_id=7, osu_username="alice", osu_user_id=1001),
            User(chat_id=HERE, telegram_id=8, osu_username="bob", osu_user_id=1002),
        ])
        await s.commit()
        for user in (await s.execute(select(User))).scalars().all():
            await refresh_user_titles(user, s)
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(UserTitleProgress).where(UserTitleProgress.title_code == "registered")) == 2
        assert await calc_title_rarity("registered", s) == 100.0

async def test_leaving_one_chat_keeps_the_scores_and_leaving_the_last_takes_them(factory):
    async with factory() as s:
        s.add_all([
            User(chat_id=HERE, telegram_id=7, osu_username="alice", osu_user_id=1001),
            User(chat_id=THERE, telegram_id=7, osu_username="alice", osu_user_id=1001),
        ])
        await s.commit()
        here, there = (await s.execute(select(User).order_by(User.chat_id.desc()))).scalars().all()
        s.add_all([
            UserBestScore(player_id=here.player_id, score_id=1, beatmap_id=1, pp=10.0),
            UserMapAttempt(player_id=here.player_id, score_id=2, beatmap_id=1, pp=0.0),
            UserTitleProgress(player_id=here.player_id, title_code="wysi", current_value=1, unlocked=True),
        ])
        await s.commit()
        assert not await clear_if_last(s, here)
        here.osu_user_id = None
        await s.commit()
        assert here.player_id is None
        assert await s.scalar(select(func.count()).select_from(UserBestScore)) == 1
        assert await clear_if_last(s, there)
        await s.commit()
        for model in (UserBestScore, UserMapAttempt, UserTitleProgress):
            assert await s.scalar(select(func.count()).select_from(model)) == 0
        assert not await clear_if_last(s, here)

@pytest.mark.sqlite_only
async def test_a_dry_run_reports_on_a_copy_and_leaves_the_database_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr(add_players, "REPORT", str(tmp_path / "players.log"))
    path = tmp_path / "bot.db"
    player_sync.switch_on(False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for table in ("user_best_scores", "user_map_attempts", "user_title_progress"):
                await conn.execute(text(f"DROP TABLE {table}"))
            for line in LEGACY:
                await conn.execute(text(line))
        await _legacy_rows(engine)
    finally:
        await engine.dispose()
    before = path.read_bytes()
    said = await move_progress_to_players.dry_run(str(path))
    assert said["user_best_scores"] == {"before": 5, "after": 3, "merged": 1, "left": 1}
    assert said["players"] == 2
    assert path.read_bytes() == before
    assert not player_sync.is_on()

@pytest.mark.sqlite_only
async def test_the_database_is_copied_before_the_move_and_only_then(tmp_path, monkeypatch):
    monkeypatch.setattr(add_players, "REPORT", str(tmp_path / "players.log"))
    monkeypatch.setattr(move_progress_to_players, "REPORT", str(tmp_path / "progress.log"))
    path = tmp_path / "bot.db"
    player_sync.switch_on(False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for table in ("user_best_scores", "user_map_attempts", "user_title_progress"):
                await conn.execute(text(f"DROP TABLE {table}"))
            for line in LEGACY:
                await conn.execute(text(line))
        await _legacy_rows(engine)
        await run_players_migration(engine)
        await run_player_progress_migration(engine)
        copies = [name for name in tmp_path.iterdir() if ".bak-before-player-progress-" in name.name]
        assert len(copies) == 1
        await run_player_progress_migration(engine)
        assert len([name for name in tmp_path.iterdir() if ".bak-before-player-progress-" in name.name]) == 1
    finally:
        await engine.dispose()
