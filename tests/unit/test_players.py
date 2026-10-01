from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import player_sync
from db.database import Base
from db.migrations import add_players
from db.migrations.add_players import Row, plan, run_players_migration
from db.models.chat_member import ChatMember
from db.models.player import Player
from db.models.user import User

CHAT_A = -100
CHAT_B = -200
CHAT_C = -300

@pytest.fixture(autouse=True)
def sync_off_between_tests():
    player_sync.switch_on(False)
    yield
    player_sync.switch_on()

@pytest_asyncio.fixture
async def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(add_players, "REPORT", str(tmp_path / "players_migration.log"))
    made = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with made.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield made
    await made.dispose()

@pytest_asyncio.fixture
async def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)

def row(id, chat, telegram, osu, fresh="", **values):
    return Row(id=id, chat_id=chat, telegram_id=telegram, osu_user_id=osu, fresh=fresh, values=values)

def test_one_person_in_two_chats_is_one_player_with_two_memberships():
    made = plan([row(1, CHAT_A, 7, 1001), row(2, CHAT_B, 7, 1001)], set())
    assert set(made.players) == {1001}
    assert made.players[1001][0] == 7
    assert sorted(made.members) == [(1, CHAT_A, 1001), (2, CHAT_B, 1001)]
    assert made.detached == []

def test_a_person_with_two_osu_accounts_keeps_the_latest_as_theirs():
    made = plan([row(1, CHAT_A, 7, 1001), row(5, CHAT_B, 7, 2002)], set())
    assert set(made.players) == {2002}
    assert made.members == [(5, CHAT_B, 2002)]
    assert made.detached == [(1, "telegram 7 plays as osu! 2002")]

def test_an_osu_account_claimed_twice_goes_to_the_one_who_signed_in_with_osu():
    rows = [row(1, CHAT_A, 7, 1001), row(2, CHAT_B, 8, 1001)]
    assert plan(rows, set()).players[1001][0] == 8, "without OAuth the latest claim wins"
    signed = plan(rows, {7})
    assert signed.players[1001][0] == 7
    assert signed.detached == [(2, "osu! 1001 belongs to telegram 7")]

def test_a_row_without_osu_is_left_alone():
    made = plan([row(1, CHAT_A, 7, None)], set())
    assert made.players == {} and made.members == []
    assert made.detached == [(1, "no osu! account")]

def test_the_player_takes_the_freshest_statistics():
    rows = [row(1, CHAT_A, 7, 1001, "2026-09-01", player_pp=100), row(2, CHAT_B, 7, 1001, "2026-09-20", player_pp=150)]
    assert plan(rows, set()).players[1001][1].values["player_pp"] == 150

async def _seed(factory, *users):
    async with factory() as session:
        session.add_all(users)
        await session.commit()

@pytest.mark.asyncio
async def test_the_migration_joins_rows_to_players_once_and_then_keeps_them_in_step(engine, factory):
    await _seed(
        factory,
        User(chat_id=CHAT_A, telegram_id=7, osu_username="alice", osu_user_id=1001, player_pp=100),
        User(chat_id=CHAT_B, telegram_id=7, osu_username="alice", osu_user_id=1001, player_pp=100),
        User(chat_id=CHAT_A, telegram_id=8, osu_username="bob", osu_user_id=2002, player_pp=50),
        User(chat_id=CHAT_C, telegram_id=9, osu_username="ghost", osu_user_id=None),
    )
    said = await run_players_migration(engine)
    assert said["players"] == 2 and said["members"] == 3
    assert said["detached"] == [{"user_id": 4, "why": "no osu! account"}]

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Player)) == 2
        assert await session.scalar(select(func.count()).select_from(ChatMember)) == 3
        linked = (await session.execute(select(User.player_id).where(User.telegram_id == 7))).scalars().all()
        assert len(set(linked)) == 1 and None not in linked

    assert await run_players_migration(engine) is None, "the migration ran twice"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Player)) == 2

    async with factory() as session:
        alice = (await session.execute(select(User).where(User.chat_id == CHAT_A, User.telegram_id == 7))).scalar_one()
        alice.player_pp = 180
        await session.commit()
    async with factory() as session:
        player = (await session.execute(select(Player).where(Player.osu_user_id == 1001))).scalar_one()
        other = (await session.execute(select(User).where(User.chat_id == CHAT_B, User.telegram_id == 7))).scalar_one()
        assert player.player_pp == 180
        assert other.player_pp == 180, "the same player in another chat was left behind"

@pytest.mark.asyncio
async def test_a_new_registration_in_two_chats_at_once_makes_one_player(factory):
    player_sync.switch_on()
    await _seed(
        factory,
        User(chat_id=CHAT_A, telegram_id=7, osu_username="alice", osu_user_id=1001, player_pp=90),
        User(chat_id=CHAT_B, telegram_id=7, osu_username="alice", osu_user_id=1001, player_pp=90),
    )
    async with factory() as session:
        players = (await session.execute(select(Player))).scalars().all()
        assert [(player.osu_user_id, player.telegram_id, player.player_pp) for player in players] == [(1001, 7, 90)]
        members = (await session.execute(select(ChatMember.chat_id).order_by(ChatMember.chat_id))).scalars().all()
        assert members == [CHAT_B, CHAT_A]

@pytest.mark.asyncio
async def test_someone_elses_osu_account_is_not_joined_to_their_player(factory):
    player_sync.switch_on()
    await _seed(factory, User(chat_id=CHAT_A, telegram_id=7, osu_username="alice", osu_user_id=1001))
    await _seed(factory, User(chat_id=CHAT_B, telegram_id=8, osu_username="alice", osu_user_id=1001))
    async with factory() as session:
        stray = (await session.execute(select(User).where(User.telegram_id == 8))).scalar_one()
        assert stray.player_id is None
        assert await session.scalar(select(func.count()).select_from(ChatMember)) == 1

@pytest.mark.asyncio
async def test_unlinking_the_last_chat_frees_the_person_for_another_account(factory):
    player_sync.switch_on()
    await _seed(factory, User(chat_id=CHAT_A, telegram_id=7, osu_username="alice", osu_user_id=1001))
    async with factory() as session:
        alice = (await session.execute(select(User).where(User.telegram_id == 7))).scalar_one()
        alice.osu_user_id = None
        alice.player_pp = 0
        await session.commit()
    async with factory() as session:
        old = (await session.execute(select(Player).where(Player.osu_user_id == 1001))).scalar_one()
        assert old.telegram_id is None
        assert old.player_pp != 0, "the unlinked chat row's zeroes reached the player"
        assert await session.scalar(select(func.count()).select_from(ChatMember)) == 0
    async with factory() as session:
        alice = (await session.execute(select(User).where(User.telegram_id == 7))).scalar_one()
        alice.osu_user_id = 3003
        alice.osu_username = "alice2"
        await session.commit()
    async with factory() as session:
        new = (await session.execute(select(Player).where(Player.osu_user_id == 3003))).scalar_one()
        assert new.telegram_id == 7
        member = (await session.execute(select(ChatMember))).scalar_one()
        assert member.player_id == new.id

@pytest.mark.asyncio
async def test_the_dry_run_writes_nothing(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"
    made = create_async_engine(url)
    async with made.begin() as conn:
        await conn.run_sync(User.__table__.create)
    async with async_sessionmaker(made)() as session:
        session.add(User(chat_id=CHAT_A, telegram_id=7, osu_username="alice", osu_user_id=1001))
        await session.commit()
    await made.dispose()
    said = await add_players.dry_run(url)
    assert said["players"] == 1 and said["members"] == 1
    made = create_async_engine(url)
    async with made.connect() as conn:
        tables = {found[0] for found in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all()}
    await made.dispose()
    assert "players" not in tables
