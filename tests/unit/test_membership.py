import types
from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.best_score import UserBestScore
from db.models.chat_member import ChatMember
from db.models.dm_active_tenant import DmActiveTenant
from db.models.leaderboard_snapshot import LeaderboardSnapshot
from db.models.left_member import LeftMember
from db.models.player import Player
from db.models.user import User
from services import membership

LOUNGE, CREW = -1001, -1002
NOW = datetime(2026, 10, 2, 12, 0)
BOT = 999

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

class _Refused(Exception):
    pass

class _Bot:
    def __init__(self, here):
        self.here = here
        self.asked = []

    async def get_me(self):
        return types.SimpleNamespace(id=BOT)

    async def get_chat_member(self, chat_id, telegram_id):
        self.asked.append((chat_id, telegram_id))
        said = self.here.get((chat_id, telegram_id), "left")
        if said == "error":
            raise _Refused("no such chat")
        if isinstance(said, tuple):
            return types.SimpleNamespace(status=said[0], is_member=said[1])
        return types.SimpleNamespace(status=said)

def _user(chat, tg, name, **kw):
    base = dict(chat_id=chat, telegram_id=tg, osu_username=name, osu_user_id=tg * 10, player_pp=1000, country="ru", accuracy=97.0)
    base.update(kw)
    return User(**base)

async def _seed(factory):
    async with factory() as s:
        naum, koto, lumen = _user(LOUNGE, 7, "NaumRedlo", hps_points=120, duel_wins=4), _user(LOUNGE, 8, "kotofey", hps_points=45, duel_wins=2, duel_losses=1), _user(LOUNGE, 9, "lumen")
        s.add_all([naum, koto, lumen, _user(CREW, 7, "NaumRedlo", hps_points=10)])
        await s.flush()
        s.add_all([
            LeaderboardSnapshot(tenant_chat_id=LOUNGE, user_id=koto.id, period_key="2026-W40", player_pp=1000),
            DmActiveTenant(telegram_id=8, chat_id=LOUNGE),
            UserBestScore(player_id=koto.player_id, score_id=1, beatmap_id=1, pp=300.0, accuracy=99.0, rank="S"),
        ])
        (await s.get(Player, koto.player_id)).pinned_chat_id = LOUNGE
        (await s.get(Player, koto.player_id)).pinned_at = NOW - timedelta(days=3)
        await s.commit()
        return naum.id, koto.id, lumen.id

def _everyone(changed=None):
    here = {(LOUNGE, BOT): "administrator", (CREW, BOT): "member", (LOUNGE, 7): "creator", (LOUNGE, 8): "member", (LOUNGE, 9): "member", (CREW, 7): "member"}
    here.update(changed or {})
    return here

async def _names(factory, chat):
    async with factory() as s:
        return sorted((await s.execute(select(User.osu_username).where(User.chat_id == chat))).scalars().all())

async def test_whoever_left_a_chat_is_no_longer_counted_there_and_keeps_what_is_shared(factory):
    _, koto, _ = await _seed(factory)
    said = await membership.sweep(_Bot(_everyone({(LOUNGE, 8): "left"})), factory, pause=0, now=NOW)
    lounge = next(told for told in said if told.chat_id == LOUNGE)
    assert (lounge.held, lounge.present, lounge.removed, lounge.skipped) == (3, 2, ["kotofey"], "")
    assert await _names(factory, LOUNGE) == ["NaumRedlo", "lumen"]
    async with factory() as s:
        assert (await s.execute(select(ChatMember).where(ChatMember.user_id == koto))).first() is None
        assert (await s.execute(select(LeaderboardSnapshot).where(LeaderboardSnapshot.user_id == koto))).first() is None
        assert (await s.execute(select(DmActiveTenant).where(DmActiveTenant.telegram_id == 8))).first() is None
        player = (await s.execute(select(Player).where(Player.telegram_id == 8))).scalar_one()
        assert (player.osu_username, player.pinned_chat_id, player.pinned_at) == ("kotofey", None, None)
        assert (await s.execute(select(UserBestScore).where(UserBestScore.player_id == player.id))).first() is not None, "what is shared went with the chat row"
        held = (await s.execute(select(LeftMember))).scalar_one()
        assert (held.chat_id, held.telegram_id, held.osu_username, held.left_at) == (LOUNGE, 8, "kotofey", NOW)

async def test_a_kicked_or_restricted_out_member_is_gone_and_a_restricted_member_stays(factory):
    await _seed(factory)
    await membership.sweep(_Bot(_everyone({(LOUNGE, 8): "kicked", (LOUNGE, 9): ("restricted", True)})), factory, pause=0, now=NOW)
    assert await _names(factory, LOUNGE) == ["NaumRedlo", "lumen"]
    await membership.sweep(_Bot(_everyone({(LOUNGE, 8): "left", (LOUNGE, 9): ("restricted", False)})), factory, pause=0, now=NOW)
    assert await _names(factory, LOUNGE) == ["NaumRedlo"]

async def test_what_telegram_does_not_say_plainly_removes_nobody(factory):
    await _seed(factory)
    said = await membership.sweep(_Bot(_everyone({(LOUNGE, 8): "error", (LOUNGE, 9): "something new"})), factory, pause=0, now=NOW)
    assert next(told for told in said if told.chat_id == LOUNGE).unknown == 2
    assert len(await _names(factory, LOUNGE)) == 3
    said = await membership.sweep(_Bot(_everyone({(LOUNGE, BOT): "left"})), factory, pause=0, now=NOW)
    assert next(told for told in said if told.chat_id == LOUNGE).skipped == membership.NO_BOT
    said = await membership.sweep(_Bot(_everyone({(LOUNGE, BOT): "error"})), factory, pause=0, now=NOW)
    assert next(told for told in said if told.chat_id == LOUNGE).skipped == membership.NO_BOT
    said = await membership.sweep(_Bot(_everyone({(LOUNGE, 7): "left", (LOUNGE, 8): "left", (LOUNGE, 9): "left"})), factory, pause=0, now=NOW)
    lounge = next(told for told in said if told.chat_id == LOUNGE)
    assert (lounge.skipped, lounge.removed) == (membership.EVERYONE, [])
    assert len(await _names(factory, LOUNGE)) == 3

async def test_a_known_player_found_in_a_chat_is_counted_there(factory):
    await _seed(factory)
    said = await membership.sweep(_Bot(_everyone({(CREW, 8): "member"})), factory, pause=0, now=NOW)
    assert next(told for told in said if told.chat_id == CREW).added == ["kotofey"]
    assert await _names(factory, CREW) == ["NaumRedlo", "kotofey"]
    async with factory() as s:
        row = (await s.execute(select(User).where(User.chat_id == CREW, User.telegram_id == 8))).scalar_one()
        player = (await s.execute(select(Player).where(Player.telegram_id == 8))).scalar_one()
        assert (row.player_id, row.osu_user_id, row.player_pp, row.hps_points) == (player.id, 80, 1000, 0)
        assert (await s.execute(select(ChatMember).where(ChatMember.chat_id == CREW, ChatMember.player_id == player.id))).first() is not None
    again = await membership.sweep(_Bot(_everyone({(CREW, 8): "member"})), factory, pause=0, now=NOW)
    assert next(told for told in again if told.chat_id == CREW).added == []

async def test_whoever_comes_back_gets_their_chat_standing_back(factory):
    await _seed(factory)
    await membership.sweep(_Bot(_everyone({(LOUNGE, 8): "left"})), factory, pause=0, now=NOW)
    said = await membership.sweep(_Bot(_everyone()), factory, pause=0, now=NOW + timedelta(days=2))
    assert next(told for told in said if told.chat_id == LOUNGE).added == ["kotofey"]
    async with factory() as s:
        row = (await s.execute(select(User).where(User.chat_id == LOUNGE, User.telegram_id == 8))).scalar_one()
        assert (row.hps_points, row.duel_wins, row.duel_losses) == (45, 2, 1)
        assert (await s.execute(select(LeftMember))).first() is None

async def test_a_check_changes_nothing(factory):
    await _seed(factory)
    said = await membership.sweep(_Bot(_everyone({(LOUNGE, 8): "left", (CREW, 9): "member"})), factory, dry=True, pause=0, now=NOW)
    assert next(told for told in said if told.chat_id == LOUNGE).removed == ["kotofey"]
    assert next(told for told in said if told.chat_id == CREW).added == ["lumen"]
    assert (len(await _names(factory, LOUNGE)), len(await _names(factory, CREW))) == (3, 1)

async def test_leaving_and_joining_are_heard_at_once(factory):
    await _seed(factory)
    assert await membership.gone(factory, LOUNGE, 9) is True
    assert await membership.gone(factory, LOUNGE, 9) is False
    assert await _names(factory, LOUNGE) == ["NaumRedlo", "kotofey"]
    assert await membership.came(factory, LOUNGE, 9) is True
    assert await membership.came(factory, LOUNGE, 9) is False
    assert await membership.came(factory, LOUNGE, 12345) is False, "a stranger was counted"
    assert await _names(factory, LOUNGE) == ["NaumRedlo", "kotofey", "lumen"]

async def test_a_chat_everyone_left_is_still_looked_at(factory):
    await _seed(factory)
    await membership.gone(factory, CREW, 7)
    async with factory() as s:
        assert await membership.chats_known(s) == [LOUNGE, CREW] or await membership.chats_known(s) == [CREW, LOUNGE]
    said = await membership.sweep(_Bot(_everyone()), factory, pause=0, now=NOW)
    assert next(told for told in said if told.chat_id == CREW).added == ["NaumRedlo"]
    async with factory() as s:
        assert (await s.execute(select(User).where(User.chat_id == CREW, User.telegram_id == 7))).scalar_one().hps_points == 10
