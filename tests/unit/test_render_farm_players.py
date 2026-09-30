import types
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import player_sync
from db.database import Base
from db.models.best_score import UserBestScore
from db.models.player import Player
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from services.render_farm import http, invites, players

GROUP = -1001
OTHER = -2002
NOW = datetime(2026, 10, 1, 12, 0)

@pytest.fixture(autouse=True)
def syncing():
    player_sync.switch_on()
    yield
    player_sync.switch_on(False)

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

def _user(chat, tg, name, **kw):
    base = dict(chat_id=chat, telegram_id=tg, osu_username=name, osu_user_id=tg * 10, player_pp=1000,
                global_rank=50_000, country="ru", accuracy=97.0, play_count=1000, play_time=7200,
                ranked_score=1_000_000, total_hits=400_000, rank="Candidate")
    base.update(kw)
    return User(**base)

async def _seed(factory):
    async with factory() as s:
        naum_here = _user(GROUP, 7, "NaumRedlo", player_pp=9870, last_api_update=NOW - timedelta(days=2))
        naum_there = _user(OTHER, 7, "NaumRedlo", player_pp=9870, last_api_update=NOW - timedelta(days=1))
        koto = _user(OTHER, 8, "kotofey", player_pp=12480)
        s.add_all([naum_here, naum_there, koto])
        await s.flush()
        s.add_all([
            UserTitleProgress(user_id=naum_here.id, title_code="wysi", current_value=1, unlocked=True, unlocked_at=NOW),
            UserTitleProgress(user_id=naum_there.id, title_code="perfectionist", current_value=1, unlocked=True, unlocked_at=NOW),
            UserBestScore(user_id=naum_there.id, score_id=1, beatmap_id=1, pp=412.6, accuracy=98.5, rank="S",
                          artist="xi", title="FREEDOM DiVE", version="FOUR DIMENSIONS", created_at=NOW),
        ])
        await s.commit()
    async with factory() as s:
        return {player.osu_username: player.id for player in (await s.execute(select(Player))).scalars().all()}

async def test_every_player_comes_once_with_what_they_earned_in_every_chat(factory):
    ids = await _seed(factory)
    async with factory() as s:
        body = await players.everyone(s, 7, now=NOW)
    names = [card["name"] for card in body["people"]]
    assert names == ["kotofey", "NaumRedlo"], "a player in two chats came twice or out of order"
    naum = body["people"][1]
    assert naum["player"] == ids["NaumRedlo"] and naum["you"] is True
    assert naum["titles"] == ["perfectionist", "wysi"], "titles earned in one of the chats were lost"
    assert naum["top"] and naum["top"][0]["pp"] == pytest.approx(412.6)
    async with factory() as s:
        found = await players.everyone(s, 7, query=" KOTO ", now=NOW)
    assert [card["name"] for card in found["people"]] == ["kotofey"]

async def test_a_player_opens_whichever_chat_they_are_in(factory):
    ids = await _seed(factory)
    async with factory() as s:
        body = await players.one(s, ids["kotofey"], 7, now=NOW)
        assert body["name"] == "kotofey" and body["player"] == ids["kotofey"] and body["you"] is False
        assert await players.one(s, 999, 7, now=NOW) is None

async def test_a_chat_is_pinned_once_a_month(factory):
    await _seed(factory)
    async with factory() as s:
        verdict, body = await players.pin(s, 7, GROUP, now=NOW)
        assert verdict == players.PINNED and body["chat"] == GROUP
        verdict, body = await players.pin(s, 7, OTHER, now=NOW + timedelta(days=10))
        assert verdict == players.TOO_SOON and body["chat"] == GROUP
        assert body["free_at"] == int((NOW + players.PIN_EVERY - datetime(1970, 1, 1)).total_seconds())
        verdict, body = await players.pin(s, 7, OTHER, now=NOW + timedelta(days=31))
        assert verdict == players.PINNED and body["chat"] == OTHER
        assert (await players.pin(s, 8, GROUP, now=NOW))[0] == players.NOT_THERE, "pinned a chat they are not in"
        assert (await players.pin(s, 5, GROUP, now=NOW))[0] == players.NOT_REGISTERED
        assert await players.pinned_chat(s, 7) == OTHER

class _Bot:
    async def get_chat(self, chat_id):
        return types.SimpleNamespace(title="Osu Squad", full_name="", photo=None, username="", first_name="", last_name="")

    async def get_chat_member(self, chat_id, telegram_id):
        return types.SimpleNamespace(status="member" if (chat_id, telegram_id) in {(GROUP, 7), (OTHER, 7), (OTHER, 8)} else "left")

@pytest_asyncio.fixture
async def served(monkeypatch, factory):
    import db.database

    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "a-shared-secret")
    monkeypatch.setattr(db.database, "AsyncSessionFactory", factory)
    token = "a-personal-token-of-sixty-four-characters-more-or-less-long-x"
    invites.remember(token, invites.Owner(7, "Naum"))
    http.set_bot(_Bot())
    app = web.Application()
    app.add_routes(http.make_routes())
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    await client.close()
    http.set_bot(None)
    invites.forget(invites.digest(token))

async def test_the_app_lists_players_and_pins_its_chat(served, factory):
    client, headers = served
    ids = await _seed(factory)
    listed = await (await client.get("/render/players", headers=headers)).json()
    assert [card["name"] for card in listed["people"]] == ["kotofey", "NaumRedlo"]
    opened = await client.get(f"/render/players/{ids['kotofey']}", headers=headers)
    assert opened.status == 200 and (await opened.json())["name"] == "kotofey"
    assert (await client.get("/render/players/999", headers=headers)).status == 404

    assert (await (await client.get("/render/me/pin", headers=headers)).json())["chat"] is None
    pinned = await client.post("/render/me/pin", json={"chat": OTHER}, headers=headers)
    assert pinned.status == 200 and (await pinned.json())["chat"] == OTHER
    refused = await client.post("/render/me/pin", json={"chat": GROUP}, headers=headers)
    assert refused.status == 409 and (await refused.json())["error"] == players.TOO_SOON
    stranger = await client.post("/render/me/pin", json={"chat": -3003}, headers=headers)
    assert stranger.status == 403

    community = await (await client.get("/render/community", headers=headers)).json()
    assert community["chat"] == OTHER, "the community did not open in the pinned chat"
