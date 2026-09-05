from __future__ import annotations

import contextlib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.user import User
from db.models.oauth_token import OAuthToken

from db.models.title_progress import UserTitleProgress
from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
import services.leaderboard.service as lb
from utils.osu.resolve_user import (
    get_registered_user,
    get_any_user_by_telegram_id,
    get_registered_user_by_osu,
    get_identity_user,
)

CHAT_A = -100
CHAT_B = -200

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def _seed_two_groups(factory):
    async with factory() as s:
        s.add_all([
            User(chat_id=CHAT_A, telegram_id=1, osu_username="alice", osu_user_id=1001,
                 player_pp=5000, hps_points=100, country="US"),
            User(chat_id=CHAT_B, telegram_id=1, osu_username="alice", osu_user_id=1001,
                 player_pp=9000, hps_points=500, country="US"),
            User(chat_id=CHAT_A, telegram_id=2, osu_username="bob", osu_user_id=1002,
                 player_pp=3000, hps_points=300, country="DE"),
        ])
        await s.commit()

@pytest.mark.asyncio
async def test_leaderboard_pp_isolated_per_group(factory):
    await _seed_two_groups(factory)
    async with factory() as s:
        a = await lb._build_entries(s, "pp", CHAT_A)
        b = await lb._build_entries(s, "pp", CHAT_B)

    assert {e["username"] for e in a} == {"alice", "bob"}
    a_alice = next(e for e in a if e["username"] == "alice")
    assert a_alice["sub_value"] == "5,000pp"
    assert [e["username"] for e in b] == ["alice"]
    assert b[0]["sub_value"] == "9,000pp"

@pytest.mark.asyncio
async def test_leaderboard_count_isolated(factory):
    await _seed_two_groups(factory)
    async with factory() as s:
        assert await lb._count_for_category(s, "pp", CHAT_A) == 2
        assert await lb._count_for_category(s, "pp", CHAT_B) == 1

@pytest.mark.asyncio
async def test_leaderboard_unknown_group_is_empty(factory):
    await _seed_two_groups(factory)
    async with factory() as s:
        assert await lb._build_entries(s, "pp", -999) == []
        assert await lb._count_for_category(s, "pp", -999) == 0

@pytest.mark.asyncio
async def test_resolve_user_scoped_by_chat(factory):
    await _seed_two_groups(factory)
    async with factory() as s:
        ua = await get_registered_user(s, telegram_id=1, chat_id=CHAT_A)
        ub = await get_registered_user(s, telegram_id=1, chat_id=CHAT_B)
        assert ua is not None and ub is not None

        assert ua.id != ub.id
        assert ua.player_pp == 5000 and ub.player_pp == 9000

        assert await get_registered_user(s, telegram_id=1, chat_id=-7) is None

@pytest.mark.asyncio
async def test_resolve_by_osu_scoped_by_chat(factory):
    await _seed_two_groups(factory)
    async with factory() as s:
        a = await get_registered_user_by_osu(s, CHAT_A, osu_user_id=1001)
        b = await get_registered_user_by_osu(s, CHAT_B, osu_user_id=1001)
        assert a.chat_id == CHAT_A and b.chat_id == CHAT_B

        assert await get_registered_user_by_osu(s, CHAT_B, osu_user_id=1002) is None

@pytest.mark.asyncio
async def test_any_user_by_tg_does_not_raise_multipleresults(factory):
    await _seed_two_groups(factory)
    async with factory() as s:
        ua = await get_any_user_by_telegram_id(s, telegram_id=1, chat_id=CHAT_A)
        assert ua is not None and ua.chat_id == CHAT_A

@pytest.mark.asyncio
async def test_identity_user_is_cross_group(factory):
    await _seed_two_groups(factory)
    async with factory() as s:
        u = await get_identity_user(s, telegram_id=1)
        assert u is not None and u.telegram_id == 1

        assert u.chat_id == CHAT_B

def _patch_tm_db(factory):
    import services.oauth.token_manager as tm

    @contextlib.asynccontextmanager
    async def _fake():
        async with factory() as s:
            yield s
    return patch.object(tm, "get_db_session", _fake)

@pytest.mark.asyncio
async def test_oauth_has_token_is_global_by_telegram_id(factory):
    from services.oauth.token_manager import has_oauth

    await _seed_two_groups(factory)
    async with factory() as s:
        s.add(OAuthToken(
            telegram_id=1,
            access_token_enc=b"x",
            refresh_token_enc=b"y",
            token_expiry=datetime.now(timezone.utc) + timedelta(days=1),
            scopes="public",
        ))
        await s.commit()

    with _patch_tm_db(factory):

        assert await has_oauth(1) is True

        assert await has_oauth(2) is False

class _FakeMsg:
    def __init__(self):
        self.text = None

    async def edit_text(self, text, **kw):
        self.text = text

class _FakeCb:
    def __init__(self, data, msg):
        self.data = data
        self.message = msg
        self.from_user = type("U", (), {"id": 999})()
        self.answered = False

    async def answer(self, *a, **k):
        self.answered = True

def _patch_misc_db(factory):
    import bot.handlers.admin.misc as misc

    @contextlib.asynccontextmanager
    async def _fake():
        async with factory() as s:
            yield s
    return patch.object(misc, "get_db_session", _fake)

async def _add_global_token(factory, telegram_id):
    async with factory() as s:
        s.add(OAuthToken(telegram_id=telegram_id, access_token_enc=b"x",
                         token_expiry=None, scopes="public"))
        await s.commit()

async def _row_id(factory, chat_id, telegram_id):
    async with factory() as s:
        return (await s.execute(
            lb.select(User).where(User.chat_id == chat_id, User.telegram_id == telegram_id)
        )).scalar_one().id

async def _run_purge(factory, target_row_id):
    import bot.handlers.admin.misc as misc
    cid = "cid-" + str(target_row_id)
    misc._PURGE_PENDING[cid] = target_row_id
    cb = _FakeCb(f"purge_confirm:{cid}", _FakeMsg())
    with _patch_misc_db(factory):
        await misc.purge_confirm(cb)
    return cb

@pytest.mark.asyncio
async def test_purge_one_group_keeps_other_row_and_oauth(factory):

    await _seed_two_groups(factory)
    await _add_global_token(factory, 1)

    await _run_purge(factory, await _row_id(factory, CHAT_A, 1))

    async with factory() as s:
        rows = (await s.execute(lb.select(User).where(User.telegram_id == 1))).scalars().all()
        toks = (await s.execute(lb.select(OAuthToken).where(OAuthToken.telegram_id == 1))).scalars().all()

    assert [r.chat_id for r in rows] == [CHAT_B]
    assert len(toks) == 1

@pytest.mark.asyncio
async def test_purge_last_group_removes_oauth(factory):
    await _seed_two_groups(factory)
    await _add_global_token(factory, 1)

    await _run_purge(factory, await _row_id(factory, CHAT_A, 1))
    await _run_purge(factory, await _row_id(factory, CHAT_B, 1))

    async with factory() as s:
        rows = (await s.execute(lb.select(User).where(User.telegram_id == 1))).scalars().all()
        toks = (await s.execute(lb.select(OAuthToken).where(OAuthToken.telegram_id == 1))).scalars().all()

    assert rows == []
    assert toks == []
