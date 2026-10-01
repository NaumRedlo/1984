import hashlib
import os
import struct
from datetime import datetime, timedelta

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.chat_member import ChatMember
from db.models.player import Player
from db.models.shared_replay import SharedReplay
from db.models.user import User
from services.render_farm import http, invites, replays
from utils.osr import header

CHAT = -1001
OTHER = -2002
MAP = "a" * 32
EPOCH = datetime(1, 1, 1)

def _words(text: str) -> bytes:
    raw = text.encode()
    return b"\x0b" + bytes([len(raw)]) + raw

def an_osr(player="kotofey", beatmap=MAP, played=datetime(2026, 9, 30, 18, 0), score=1_234_567, mode=0, salt=0) -> bytes:
    ticks = int((played - EPOCH).total_seconds() * 10_000_000)
    return (
        bytes([mode]) + struct.pack("<i", 20_250_101) + _words(beatmap) + _words(player) + _words("b" * 32)
        + struct.pack("<6H", 900, 12, 1, 80, 9, 2) + struct.pack("<i", score) + struct.pack("<H", 777) + b"\x00" + struct.pack("<i", 72)
        + _words("0|1,100|1") + struct.pack("<q", ticks) + struct.pack("<i", 4) + bytes([salt, 1, 2, 3])
    )

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def _seed(factory, sharing=("kotofey", "lumen", "elsewhere")):
    people = {}
    async with factory() as s:
        for telegram, name, chat in ((7, "NaumRedlo", CHAT), (8, "kotofey", CHAT), (9, "lumen", CHAT), (10, "elsewhere", OTHER)):
            player = Player(osu_user_id=telegram * 10, telegram_id=telegram, osu_username=name, country="ru", share_replays=name in sharing)
            s.add(player)
            await s.flush()
            user = User(chat_id=chat, telegram_id=telegram, osu_username=name, osu_user_id=telegram * 10, rank="Candidate", player_id=player.id)
            s.add(user)
            await s.flush()
            s.add(ChatMember(chat_id=chat, player_id=player.id, user_id=user.id))
            people[name] = player.id
        await s.commit()
    return people

def test_the_header_now_tells_when_the_play_happened():
    head = header(an_osr())
    assert head.played_at == datetime(2026, 9, 30, 18, 0) and head.player == "kotofey" and head.mods == 72

async def test_a_shared_replay_is_kept_once_with_what_the_app_said_about_its_map(factory, tmp_path):
    await _seed(factory)
    data = an_osr()
    named = {"artist": "xi", "title": "FREEDOM DiVE", "version": "FOUR DIMENSIONS", "beatmapset": 39804}
    async with factory() as s:
        assert await replays.keep(s, 8, data, named, folder=str(tmp_path)) == replays.KEPT
        assert await replays.keep(s, 8, data, named, folder=str(tmp_path)) == replays.KNOWN
        row = (await s.execute(select(SharedReplay))).scalars().one()
    assert (row.artist, row.title, row.version, row.beatmapset_id) == ("xi", "FREEDOM DiVE", "FOUR DIMENSIONS", 39804)
    assert (row.score, row.combo, row.count_miss, row.played_at) == (1_234_567, 777, 2, datetime(2026, 9, 30, 18, 0))
    assert os.path.isfile(replays.path_of(hashlib.md5(data).hexdigest(), str(tmp_path)))

async def test_only_the_player_s_own_standard_plays_are_taken_and_only_while_sharing(factory, tmp_path):
    await _seed(factory, sharing=("kotofey",))
    async with factory() as s:
        assert await replays.keep(s, 8, an_osr(player="someone else"), folder=str(tmp_path)) == replays.NOT_YOURS
        assert await replays.keep(s, 8, an_osr(player="KOTOFEY", salt=1), folder=str(tmp_path)) == replays.KEPT
        assert await replays.keep(s, 8, an_osr(mode=3), folder=str(tmp_path)) == replays.NOT_A_REPLAY
        assert await replays.keep(s, 8, b"nothing like a replay", folder=str(tmp_path)) == replays.NOT_A_REPLAY
        assert await replays.keep(s, 7, an_osr(player="NaumRedlo"), folder=str(tmp_path)) == replays.NOT_SHARING
        assert await replays.keep(s, 99, an_osr(), folder=str(tmp_path)) == replays.NOT_REGISTERED
        assert await replays.keep(s, 8, an_osr(salt=2), folder=str(tmp_path), most=10) == replays.FULL

class _Osu:
    def __init__(self):
        self.asked = []

    async def lookup_beatmap_by_checksum(self, checksum):
        self.asked.append(checksum)
        return {"version": "Extra", "beatmapset_id": 77, "beatmapset": {"artist": "xi", "title": "Blue Zenith"}}

async def test_a_map_without_a_name_is_asked_about_once(factory, tmp_path):
    await _seed(factory)
    osu = _Osu()
    async with factory() as s:
        await replays.keep(s, 8, an_osr(), osu=osu, folder=str(tmp_path))
        await replays.keep(s, 9, an_osr(player="lumen", salt=1), osu=osu, folder=str(tmp_path))
        rows = (await s.execute(select(SharedReplay).order_by(SharedReplay.id))).scalars().all()
    assert osu.asked == [MAP]
    assert [(row.title, row.version, row.beatmapset_id) for row in rows] == [("Blue Zenith", "Extra", 77)] * 2

async def test_a_player_keeps_only_the_newest_plays(factory, tmp_path):
    await _seed(factory)
    day = datetime(2026, 9, 1)
    async with factory() as s:
        for n in (1, 3, 2):
            assert await replays.keep(s, 8, an_osr(played=day + timedelta(days=n), salt=n), folder=str(tmp_path), each=2) in (replays.KEPT, replays.KNOWN)
        assert await replays.keep(s, 8, an_osr(played=day, salt=9), folder=str(tmp_path), each=2) == replays.KNOWN
        kept = [row.played_at.day for row in (await s.execute(select(SharedReplay).order_by(SharedReplay.played_at))).scalars().all()]
    assert kept == [3, 4]
    assert len([name for name in os.listdir(tmp_path) if name.endswith(".osr")]) == 2

async def test_the_journal_of_others_lists_chat_mates_first_and_everyone_on_request(factory, tmp_path):
    people = await _seed(factory)
    async with factory() as s:
        await replays.keep(s, 8, an_osr(played=datetime(2026, 9, 30)), {"artist": "xi", "title": "FREEDOM DiVE", "version": "Extra"}, folder=str(tmp_path))
        await replays.keep(s, 9, an_osr(player="lumen", played=datetime(2026, 10, 1), salt=1), folder=str(tmp_path))
        await replays.keep(s, 10, an_osr(player="elsewhere", played=datetime(2026, 10, 2), salt=2), folder=str(tmp_path))
        near = await replays.listed(s, 7)
        everyone = await replays.listed(s, 7, replays.ALL)
        own = await replays.listed(s, 8)
        stranger = await replays.listed(s, 99)
    assert [row["player"] for row in near["replays"]] == ["lumen", "kotofey"]
    assert [row["player"] for row in everyone["replays"]] == ["elsewhere", "lumen", "kotofey"]
    assert near["replays"][1]["title"] == "FREEDOM DiVE" and near["replays"][1]["player_id"] == people["kotofey"]
    assert [row["player"] for row in own["replays"]] == ["lumen"]
    assert stranger is None

async def test_switching_sharing_off_takes_the_replays_away(factory, tmp_path):
    await _seed(factory, sharing=())
    data = an_osr()
    async with factory() as s:
        assert (await replays.state(s, 8)) == {"on": False, "name": "kotofey", "count": 0, "most": replays.PLAYER_REPLAYS_EACH}
        assert (await replays.switch(s, 8, True, folder=str(tmp_path)))["on"] is True
        await replays.keep(s, 8, data, folder=str(tmp_path))
        assert (await replays.state(s, 8))["count"] == 1
        assert await replays.readable(s, 7, hashlib.md5(data).hexdigest())
        gone = await replays.switch(s, 8, False, folder=str(tmp_path))
        assert gone == {"on": False, "name": "kotofey", "count": 0, "most": replays.PLAYER_REPLAYS_EACH}
        assert not await replays.readable(s, 7, hashlib.md5(data).hexdigest())
        assert await replays.switch(s, 99, True) is None
    assert not os.path.exists(replays.path_of(hashlib.md5(data).hexdigest(), str(tmp_path)))

@pytest_asyncio.fixture
async def served(monkeypatch, factory, tmp_path):
    import db.database

    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "a-shared-secret")
    monkeypatch.setattr(replays, "PLAYER_REPLAYS_DIR", str(tmp_path / "replays"))
    monkeypatch.setattr(db.database, "AsyncSessionFactory", factory)
    http.set_osu(None)
    tokens = {}
    for telegram, name in ((7, "Naum"), (8, "Koto")):
        token = f"a-personal-token-of-sixty-four-characters-more-or-less-long-{telegram}"
        invites.remember(token, invites.Owner(telegram, name))
        tokens[telegram] = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    app = web.Application()
    app.add_routes(http.make_routes())
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, tokens
    await client.close()
    for headers in tokens.values():
        invites.forget(invites.digest(headers["Authorization"][len("Bearer "):]))

async def test_the_app_shares_reads_and_fetches_replays_over_http(served, factory):
    client, tokens = served
    await _seed(factory, sharing=())
    data = an_osr()
    assert (await client.post("/render/replays", headers=tokens[8], data=data)).status == 409
    assert (await client.post("/render/me/replays", headers=tokens[8], json={"on": "yes"})).status == 400
    assert (await (await client.post("/render/me/replays", headers=tokens[8], json={"on": True})).json())["on"] is True
    named = '{"artist": "xi", "title": "FREEDOM DiVE", "version": "Extra", "beatmapset": 39804}'
    first = await client.post("/render/replays", headers={**tokens[8], "X-Render-Meta": named}, data=data)
    assert first.status == 200 and (await first.json()) == {"ok": True, "known": False}
    assert (await (await client.post("/render/replays", headers=tokens[8], data=data)).json())["known"] is True
    assert (await client.post("/render/replays", headers=tokens[8], data=an_osr(player="other"))).status == 403
    assert (await client.post("/render/replays", headers=tokens[8], data=b"nope")).status == 400
    assert (await (await client.get("/render/me/replays", headers=tokens[8])).json())["count"] == 1
    listed = await (await client.get("/render/replays", headers=tokens[7])).json()
    row = listed["replays"][0]
    assert (row["player"], row["title"], row["size"]) == ("kotofey", "FREEDOM DiVE", len(data))
    got = await client.get(f"/render/replays/{row['hash']}", headers=tokens[7])
    assert got.status == 200 and await got.read() == data
    assert (await client.get("/render/replays/nope", headers=tokens[7])).status == 400
    assert (await client.get(f"/render/replays/{'f' * 32}", headers=tokens[7])).status == 404
    await client.post("/render/me/replays", headers=tokens[8], json={"on": False})
    assert (await client.get(f"/render/replays/{row['hash']}", headers=tokens[7])).status == 404
    assert (await (await client.get("/render/replays", headers=tokens[7])).json())["replays"] == []
