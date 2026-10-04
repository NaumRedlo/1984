import asyncio
import hashlib
import json
import os
import struct
import types
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
from db.models.shared_video import SharedVideo, VideoDelivery
from db.models.user import User
from services.render_farm import app_storage, http, invites, videos

CHAT = -1001
OTHER = -2002
NOW = datetime(2026, 10, 1, 12, 0)
EVERYBODY = {7, 8, 9, 10, 11}

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def _seed(factory, **accepts):
    people = {}
    async with factory() as s:
        for telegram, name, chat in ((7, "NaumRedlo", CHAT), (8, "kotofey", CHAT), (9, "lumen", CHAT), (10, "elsewhere", OTHER), (11, "silent", OTHER)):
            player = Player(osu_user_id=telegram * 10, telegram_id=telegram, osu_username=name, country="ru", accept_videos=accepts.get(name))
            s.add(player)
            await s.flush()
            user = User(chat_id=chat, telegram_id=telegram, osu_username=name, osu_user_id=telegram * 10, rank="Candidate", player_id=player.id)
            s.add(user)
            await s.flush()
            s.add(ChatMember(chat_id=chat, player_id=player.id, user_id=user.id))
            people[name] = player.id
        await s.commit()
    return people

def _media(unique="u1", **kw):
    base = dict(file_id=f"file-{unique}", file_unique_id=unique, file_size=4_000_000, duration=95, width=1920, height=1080,
                thumbnail=types.SimpleNamespace(file_id=f"thumb-{unique}"))
    base.update(kw)
    return types.SimpleNamespace(**base)

META = {"player": "NaumRedlo", "song": "xi — FREEDOM DiVE", "version": "FOUR DIMENSIONS", "mods": ["hd", "dt"],
        "map_hash": "A" * 32, "settings": {"width": 1920, "height": 1080, "fps": 60}}

async def _kept(factory, owner=7, unique="u1", meta=META):
    async with factory() as s:
        return await videos.keep(s, owner, "video", _media(unique), meta)

def test_the_default_is_to_take_videos_from_chat_mates():
    assert videos.accept_of(Player(accept_videos=None)) == videos.SHARED
    assert videos.accept_of(Player(accept_videos="whatever")) == videos.SHARED
    assert videos.lets(videos.SHARED, True) and not videos.lets(videos.SHARED, False)
    assert videos.lets(videos.EVERYONE, False) and not videos.lets(videos.NOBODY, True)

def test_what_the_app_says_about_a_video_is_cleaned():
    said = videos.described({**META, "settings": {"big": "x" * videos.SETTINGS_MOST}, "map_hash": "nope", "player": 5})
    assert said["mods"] == "HD,DT" and said["map_hash"] is None and said["settings"] is None and said["player"] is None
    assert videos.described(META)["map_hash"] == "a" * 32

async def test_a_sent_video_is_remembered_once(factory):
    await _seed(factory)
    first = await _kept(factory)
    again = await _kept(factory, meta={})
    assert first == again
    async with factory() as s:
        video = await s.get(SharedVideo, first)
    assert (video.file_id, video.thumb_id, video.size, video.duration) == ("file-u1", "thumb-u1", 4_000_000, 95)
    assert video.player == "NaumRedlo" and video.mods == "HD,DT"
    assert await _kept(factory, owner=8) != first

async def test_receivers_are_the_people_who_have_the_app_and_let_you(factory):
    await _seed(factory, kotofey="nobody", elsewhere="everyone")
    async with factory() as s:
        listed = await videos.receivers(s, 7, EVERYBODY)
        without_app = await videos.receivers(s, 7, {7, 10})
        stranger = await videos.receivers(s, 99, EVERYBODY)
    assert [(face["name"], face["shared"]) for face in listed] == [("lumen", True), ("elsewhere", False)]
    assert [face["name"] for face in without_app] == ["elsewhere"]
    assert stranger is None

async def test_a_video_reaches_those_who_take_it_and_tells_why_the_rest_did_not(factory):
    people = await _seed(factory, kotofey="nobody", elsewhere="everyone")
    video = await _kept(factory)
    async with factory() as s:
        said = await videos.share(s, 7, video, [people["lumen"], people["kotofey"], people["elsewhere"], people["silent"], people["NaumRedlo"], 999, people["lumen"]], EVERYBODY, now=NOW)
        not_mine = await videos.share(s, 8, video, [people["lumen"]], EVERYBODY, now=NOW)
    assert said["sent"] == [people["lumen"], people["elsewhere"]]
    assert {(row["player"], row["why"]) for row in said["refused"]} == {
        (people["kotofey"], videos.CLOSED), (people["silent"], videos.CLOSED), (people["NaumRedlo"], videos.YOURSELF), (999, videos.NO_ONE),
    }
    assert not_mine is None

async def test_someone_without_the_app_cannot_be_sent_a_video(factory):
    people = await _seed(factory)
    video = await _kept(factory)
    async with factory() as s:
        said = await videos.share(s, 7, video, [people["lumen"]], {7}, now=NOW)
    assert said == {"sent": [], "refused": [{"player": people["lumen"], "why": videos.CLOSED}]}

async def test_the_inbox_shows_what_came_and_from_whom(factory):
    people = await _seed(factory)
    video = await _kept(factory)
    async with factory() as s:
        await videos.attach_replay(s, 7, video, "b" * 32)
        await videos.share(s, 7, video, [people["lumen"]], EVERYBODY, now=NOW)
        box = await videos.inbox(s, 9, now=NOW)
        empty = await videos.inbox(s, 8, now=NOW)
        nobody = await videos.inbox(s, 99, now=NOW)
    assert box["accept"] == videos.SHARED and box["registered"] is True
    row = box["videos"][0]
    assert row["from"]["name"] == "NaumRedlo" and row["from"]["player"] == people["NaumRedlo"]
    assert (row["player"], row["song"], row["version"], row["mods"]) == ("NaumRedlo", "xi — FREEDOM DiVE", "FOUR DIMENSIONS", ["HD", "DT"])
    assert (row["duration"], row["size"], row["thumb"], row["replay"], row["seen"]) == (95, 4_000_000, True, True, False)
    assert row["settings"] == {"width": 1920, "height": 1080, "fps": 60}
    assert empty["videos"] == [] and nobody["registered"] is False

async def test_seen_and_dropped_belong_to_the_one_who_received(factory):
    people = await _seed(factory)
    video = await _kept(factory)
    async with factory() as s:
        await videos.share(s, 7, video, [people["lumen"]], EVERYBODY, now=NOW)
        delivery = (await videos.inbox(s, 9))["videos"][0]["id"]
        assert not await videos.seen(s, 8, delivery, now=NOW)
        assert await videos.seen(s, 9, delivery, now=NOW)
        assert (await videos.inbox(s, 9))["videos"][0]["seen"] is True
        await videos.share(s, 7, video, [people["lumen"]], EVERYBODY, now=NOW + timedelta(hours=1))
        again = (await videos.inbox(s, 9))["videos"]
        assert len(again) == 1 and again[0]["seen"] is False
        assert not await videos.drop(s, 8, delivery)
        assert await videos.drop(s, 9, delivery)
        assert (await videos.inbox(s, 9))["videos"] == []

async def test_a_day_has_a_limit_and_an_inbox_has_a_bottom(factory, monkeypatch):
    people = await _seed(factory, elsewhere="everyone")
    monkeypatch.setattr(videos, "DAILY", 2)
    one, two, three = [await _kept(factory, unique=f"u{n}") for n in range(3)]
    async with factory() as s:
        assert (await videos.share(s, 7, one, [people["lumen"]], EVERYBODY, now=NOW))["sent"]
        assert (await videos.share(s, 7, two, [people["lumen"]], EVERYBODY, now=NOW))["sent"]
        late = await videos.share(s, 7, three, [people["lumen"]], EVERYBODY, now=NOW)
        assert late["refused"] == [{"player": people["lumen"], "why": videos.LIMIT}]
        tomorrow = await videos.share(s, 7, three, [people["lumen"]], EVERYBODY, now=NOW + timedelta(days=1, minutes=1))
        assert tomorrow["sent"] == [people["lumen"]]
    monkeypatch.setattr(videos, "INBOX_MOST", 3)
    fourth = await _kept(factory, unique="u4")
    async with factory() as s:
        full = await videos.share(s, 7, fourth, [people["lumen"]], EVERYBODY, now=NOW + timedelta(days=3))
    assert full["refused"] == [{"player": people["lumen"], "why": videos.FULL}]

async def test_the_setting_is_kept_and_checked(factory):
    people = await _seed(factory)
    video = await _kept(factory)
    async with factory() as s:
        assert await videos.accept(s, 9, "sometimes") is None
        assert await videos.accept(s, 99, videos.NOBODY) is None
        assert await videos.accept(s, 9, videos.NOBODY) == videos.NOBODY
        assert (await videos.inbox(s, 9))["accept"] == videos.NOBODY
        said = await videos.share(s, 7, video, [people["lumen"]], EVERYBODY, now=NOW)
    assert said["refused"] == [{"player": people["lumen"], "why": videos.CLOSED}]

def test_the_caption_names_the_play_and_the_sender():
    video = SharedVideo(player="NaumRedlo", song="xi — FREEDOM DiVE", version="FOUR DIMENSIONS")
    assert videos.caption(video, Player(osu_username="kotofey")) == "NaumRedlo — xi — FREEDOM DiVE [FOUR DIMENSIONS]\nkotofey"
    assert videos.caption(SharedVideo(), None) == ""

def _replay() -> bytes:
    return bytes([0]) + struct.pack("<i", 20_250_101) + b"\x0b" + b"\x20" + b"a" * 32 + b"rest of the replay"

class _Bot:
    def __init__(self, folder):
        self.folder = folder
        self.sent = []
        self.token = "bot-token"

    async def send_video(self, chat_id, video, **kwargs):
        self.sent.append(("video", chat_id, video, kwargs))
        if isinstance(video, str):
            return types.SimpleNamespace(message_id=43)
        return types.SimpleNamespace(message_id=42, video=_media("sent"))

    async def send_animation(self, chat_id, animation, **kwargs):
        self.sent.append(("animation", chat_id, animation, kwargs))
        return types.SimpleNamespace(message_id=44)

    async def send_document(self, chat_id, document, **kwargs):
        self.sent.append(("document", chat_id, document, kwargs))
        return types.SimpleNamespace(message_id=45)

    async def get_file(self, file_id, request_timeout=None):
        path = os.path.join(self.folder, f"{file_id}.bin")
        with open(path, "wb") as out:
            out.write(b"picture" if file_id.startswith("thumb") else b"mp4-" * 1000)
        return types.SimpleNamespace(file_path=path, file_size=os.path.getsize(path))

    async def download_file(self, file_path):
        return open(file_path, "rb")

@pytest_asyncio.fixture
async def served(monkeypatch, factory, tmp_path):
    import db.database

    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "a-shared-secret")
    monkeypatch.setattr(http, "SHARED_REPLAYS_DIR", str(tmp_path / "replays"))
    monkeypatch.setattr(db.database, "AsyncSessionFactory", factory)
    http._thumb_cache.clear()
    tokens = {}
    for telegram, name in ((7, "Naum"), (9, "Lumen"), (8, "Koto")):
        token = f"a-personal-token-of-sixty-four-characters-more-or-less-long-{telegram}"
        invites.remember(token, invites.Owner(telegram, name))
        tokens[telegram] = {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}
    bot = _Bot(str(tmp_path))
    http.set_bot(bot)
    app = web.Application()
    app.add_routes(http.make_routes())
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, tokens, bot
    await client.close()
    http.set_bot(None)
    for headers in tokens.values():
        invites.forget(invites.digest(headers["Authorization"][len("Bearer "):]))

async def _sent_and_shared(served, factory):
    client, tokens, bot = served
    people = await _seed(factory)
    meta = '{"player": "NaumRedlo", "song": "xi - FREEDOM DiVE", "version": "FOUR DIMENSIONS", "mods": ["HD"], "map_hash": "%s", "settings": {"fps": 60}}' % ("c" * 32)
    reply = await client.post("/render/send", headers={**tokens[7], "X-Render-Meta": meta}, data=b"mp4-bytes")
    video = (await reply.json())["video"]
    shared = await client.post(f"/render/videos/{video}/share", headers=tokens[7], json={"to": [people["lumen"]]})
    assert (await shared.json()) == {"sent": [people["lumen"]], "refused": []}
    box = await (await client.get("/render/me/inbox", headers=tokens[9])).json()
    return video, box["videos"][0]["id"], people

async def test_sending_a_video_gives_it_a_number_to_share_by(served, factory):
    client, tokens, bot = served
    await _seed(factory)
    reply = await client.post("/render/send", headers={**tokens[7], "X-Render-Meta": '{"player": "NaumRedlo"}'}, data=b"mp4-bytes")
    body = await reply.json()
    assert reply.status == 200 and body["message_id"] == 42 and isinstance(body["video"], int)
    async with factory() as s:
        video = await s.get(SharedVideo, body["video"])
    assert (video.owner, video.kind, video.file_id, video.player) == (7, "video", "file-sent", "NaumRedlo")

async def test_the_people_to_send_to_are_listed(served, factory):
    client, tokens, _ = served
    await _seed(factory)
    body = await (await client.get("/render/videos/receivers", headers=tokens[7])).json()
    assert [face["name"] for face in body["people"]] == ["kotofey", "lumen"]

async def test_a_shared_video_lands_in_the_inbox_of_the_other_app(served, factory):
    client, tokens, _ = served
    video, delivery, people = await _sent_and_shared(served, factory)
    box = await (await client.get("/render/me/inbox", headers=tokens[9])).json()
    row = box["videos"][0]
    assert row["from"]["name"] == "NaumRedlo" and row["song"] == "xi - FREEDOM DiVE" and row["settings"] == {"fps": 60}
    assert (await (await client.get("/render/me/inbox", headers=tokens[7])).json())["videos"] == []
    assert (await client.post(f"/render/videos/{video}/share", headers=tokens[9], json={"to": [people["kotofey"]]})).status == 404
    assert (await client.post(f"/render/videos/{video}/share", headers=tokens[7], json={"to": "lumen"})).status == 400

async def test_the_replay_travels_with_the_video(served, factory):
    client, tokens, _ = served
    video, delivery, _ = await _sent_and_shared(served, factory)
    assert (await client.get(f"/render/inbox/{delivery}/replay", headers=tokens[9])).status == 404
    assert (await client.put(f"/render/videos/{video}/replay", headers=tokens[9], data=_replay())).status == 404
    assert (await client.put(f"/render/videos/{video}/replay", headers=tokens[7], data=b"not a replay at all")).status == 400
    assert (await client.put(f"/render/videos/{video}/replay", headers=tokens[7], data=_replay())).status == 200
    got = await client.get(f"/render/inbox/{delivery}/replay", headers=tokens[9])
    assert got.status == 200 and await got.read() == _replay()
    assert (await client.get(f"/render/inbox/{delivery}/replay", headers=tokens[8])).status == 404

async def test_watching_streams_the_file_and_leaves_nothing_on_the_disk(served, factory):
    client, tokens, bot = served
    _, delivery, _ = await _sent_and_shared(served, factory)
    got = await client.get(f"/render/inbox/{delivery}/video", headers=tokens[9])
    assert got.status == 200 and got.headers["Content-Length"] == "4000"
    assert await got.read() == b"mp4-" * 1000
    left = os.path.join(bot.folder, "file-sent.bin")
    for _ in range(100):
        if not os.path.exists(left):
            break
        await asyncio.sleep(0.01)
    assert not os.path.exists(left)
    assert (await client.get(f"/render/inbox/{delivery}/video", headers=tokens[8])).status == 404
    assert (await client.get("/render/inbox/abc/video", headers=tokens[9])).status == 400

async def test_the_picture_of_a_received_video_is_served(served, factory):
    client, tokens, _ = served
    _, delivery, _ = await _sent_and_shared(served, factory)
    got = await client.get(f"/render/inbox/{delivery}/thumb", headers=tokens[9])
    assert got.status == 200 and await got.read() == b"picture" and got.headers["Content-Type"] == "image/jpeg"

async def test_a_received_video_can_be_passed_to_telegram(served, factory):
    client, tokens, bot = served
    _, delivery, _ = await _sent_and_shared(served, factory)
    got = await client.post(f"/render/inbox/{delivery}/telegram", headers=tokens[9])
    assert got.status == 200
    kind, chat_id, what, kwargs = bot.sent[-1]
    assert (kind, chat_id, what) == ("video", 9, "file-sent")
    assert kwargs["caption"] == "NaumRedlo — xi - FREEDOM DiVE [FOUR DIMENSIONS]\nNaumRedlo"

async def test_seen_dropped_and_the_setting_over_http(served, factory):
    client, tokens, _ = served
    video, delivery, people = await _sent_and_shared(served, factory)
    assert (await client.post(f"/render/inbox/{delivery}/seen", headers=tokens[8])).status == 404
    assert (await client.post(f"/render/inbox/{delivery}/seen", headers=tokens[9])).status == 200
    assert (await (await client.get("/render/me/inbox", headers=tokens[9])).json())["videos"][0]["seen"] is True
    assert (await client.delete(f"/render/inbox/{delivery}", headers=tokens[9])).status == 200
    assert (await client.delete(f"/render/inbox/{delivery}", headers=tokens[9])).status == 404
    assert (await client.post("/render/me/accept", headers=tokens[9], json={"from": "friends"})).status == 400
    assert (await (await client.post("/render/me/accept", headers=tokens[9], json={"from": "nobody"})).json()) == {"accept": "nobody"}
    refused = await (await client.post(f"/render/videos/{video}/share", headers=tokens[7], json={"to": [people["lumen"]]})).json()
    assert refused == {"sent": [], "refused": [{"player": people["lumen"], "why": "closed"}]}

async def test_the_migration_adds_the_setting_to_an_older_players_table():
    from sqlalchemy import text

    from db.migrations.add_video_sharing import run_video_sharing_migration

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE players (id INTEGER PRIMARY KEY, osu_user_id INTEGER, osu_username VARCHAR(255))"))
        await run_video_sharing_migration(engine)
        await run_video_sharing_migration(engine)
        async with engine.begin() as conn:
            columns = {row[1] for row in (await conn.execute(text("PRAGMA table_info(players)"))).fetchall()}
            tables = {row[0] for row in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}
        assert "accept_videos" in columns and {"shared_videos", "video_deliveries"} <= tables
    finally:
        await engine.dispose()

async def test_without_a_local_bot_api_the_file_is_passed_on_from_telegram(served, factory, monkeypatch):
    client, tokens, bot = served
    _, delivery, _ = await _sent_and_shared(served, factory)
    asked = []

    async def get_file(file_id, request_timeout=None):
        return types.SimpleNamespace(file_path="videos/file_1.mp4", file_size=6)

    async def stream_content(url, timeout=30, chunk_size=65536):
        asked.append(url)
        yield b"abc"
        yield b"def"

    monkeypatch.setattr(bot, "get_file", get_file, raising=False)
    bot.session = types.SimpleNamespace(
        api=types.SimpleNamespace(file_url=lambda token, path: f"https://files/{token}/{path}"),
        stream_content=stream_content,
    )
    got = await client.get(f"/render/inbox/{delivery}/video", headers=tokens[9])
    assert got.status == 200 and await got.read() == b"abcdef"
    assert asked == ["https://files/bot-token/videos/file_1.mp4"]

async def test_app_video_expires_and_can_be_rendered_again(served, factory, monkeypatch, tmp_path):
    client, tokens, bot = served
    people = await _seed(factory)
    monkeypatch.setattr(app_storage, "APP_VIDEOS_DIR", str(tmp_path / "app-videos"))
    for telegram, player in ((7, people["NaumRedlo"]), (9, people["lumen"])):
        token = tokens[telegram]["Authorization"][len("Bearer "):]
        invites.remember(token, invites.Owner(telegram, "test", player))
    original = b"\x00\x00\x00\x18ftypisom" + b"original-video"
    meta = {**META, "settings": {"width": 1280, "height": 720, "fps": 120, "music": 0.5}}
    reply = await client.post("/render/videos", headers={**tokens[7], "X-Render-Meta": json.dumps(meta)}, data=original)
    assert reply.status == 200
    video_id = (await reply.json())["video"]
    assert not bot.sent
    assert (await client.post(f"/render/videos/{video_id}/share", headers=tokens[7], json={"to": [people["lumen"]]})).status == 409
    assert (await client.put(f"/render/videos/{video_id}/replay", headers=tokens[7], data=_replay())).status == 200
    async with factory() as session:
        stored = await session.get(SharedVideo, video_id)
        assert len(stored.replay_sha256) == 64
        assert app_storage.replay_path(stored, http.SHARED_REPLAYS_DIR).read_bytes() == _replay()
    shared = await client.post(f"/render/videos/{video_id}/share", headers=tokens[7], json={"to": [people["lumen"]]})
    assert (await shared.json())["sent"] == [people["lumen"]]
    box = await (await client.get("/render/me/inbox", headers=tokens[9])).json()
    row = box["videos"][0]
    delivery = row["id"]
    assert row["storage"] == "app" and row["available"] and row["expires_at"]
    assert (await client.get(f"/render/inbox/{delivery}/video", headers=tokens[8])).status == 404
    response = await client.get(f"/render/inbox/{delivery}/video", headers=tokens[9])
    assert response.headers["X-Content-SHA256"] == hashlib.sha256(original).hexdigest()
    assert await response.read() == original
    async with factory() as session:
        video = await session.get(SharedVideo, video_id)
        video.stored_until = datetime(2020, 1, 1)
        await session.commit()
        assert await app_storage.purge(session) == 1
    assert not app_storage.path_for(video_id).exists()
    assert (await client.get(f"/render/inbox/{delivery}/video", headers=tokens[9])).status == 410
    assert (await client.post(f"/render/inbox/{delivery}/restore", headers=tokens[8])).status == 404
    restore = await client.post(f"/render/inbox/{delivery}/restore", headers=tokens[9])
    assert restore.status == 202
    assert (await restore.json())["state"] == "rendering"
    claim = await client.post("/render/claim", headers=tokens[7], json={"take": True})
    job = await claim.json()
    assert job["settings"]["fps"] == 120 and job["settings"]["music"] == 0.5
    again = b"\x00\x00\x00\x18ftypisom" + b"rerendered-video"
    result = await client.post(f"/render/job/{job['id']}/result", headers=tokens[7], data=again)
    assert result.status == 200
    state = await client.get(f"/render/inbox/{delivery}/restore", headers=tokens[9])
    assert (await state.json())["state"] == "ready"
    response = await client.get(f"/render/inbox/{delivery}/video", headers=tokens[9])
    assert await response.read() == again

async def test_app_video_storage_migration_is_repeatable():
    from sqlalchemy import text

    from db.migrations.add_app_video_storage import run_app_video_storage_migration

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE shared_videos (id INTEGER PRIMARY KEY, owner BIGINT, file_id VARCHAR(255))"))
        await run_app_video_storage_migration(engine)
        await run_app_video_storage_migration(engine)
        async with engine.begin() as conn:
            columns = {row[1] for row in (await conn.execute(text("PRAGMA table_info(shared_videos)"))).fetchall()}
        assert {"storage_hash", "stored_until", "skin_name", "skin_hash", "owner_player_id", "replay_sha256"} <= columns
    finally:
        await engine.dispose()

async def test_bot_order_result_appears_in_owners_app_instead_of_telegram(served, factory, monkeypatch, tmp_path):
    client, tokens, bot = served
    await _seed(factory)
    monkeypatch.setattr(app_storage, "APP_VIDEOS_DIR", str(tmp_path / "app-videos"))
    replay = tmp_path / "bot-order.osr"
    replay.write_bytes(_replay())
    job = http.default_queue.offer(str(replay), "NaumRedlo — A Map", beatmap_md5="a" * 32,
                                   requester=7, in_app=True, video_meta={"player": "NaumRedlo", "song": "A Map"})
    claim = await client.post("/render/claim", headers=tokens[7], json={"take": True})
    assert (await claim.json())["id"] == job.id
    result = await client.post(f"/render/job/{job.id}/result", headers=tokens[7], data=b"\x00\x00\x00\x18ftypisomrender")
    assert result.status == 200
    assert job.payload["video"] and not bot.sent
    box = await (await client.get("/render/me/inbox", headers=tokens[7])).json()
    row = box["videos"][0]
    assert row["storage"] == "app" and row["player"] == "NaumRedlo"
    assert row["replay"] and row["available"]

async def test_full_video_storage_refuses_upload_without_leaving_an_inbox_item(served, factory, monkeypatch, tmp_path):
    client, tokens, _ = served
    people = await _seed(factory)
    token = tokens[7]["Authorization"][len("Bearer "):]
    invites.remember(token, invites.Owner(7, "Naum", people["NaumRedlo"]))
    monkeypatch.setattr(app_storage, "APP_VIDEOS_DIR", str(tmp_path / "app-videos"))
    monkeypatch.setattr(app_storage, "APP_VIDEO_STORAGE_MOST", 10)
    reply = await client.post("/render/videos", headers={**tokens[7], "X-Render-Meta": json.dumps(META)},
                              data=b"\x00\x00\x00\x18ftypisomtoo-large-for-storage")
    assert reply.status == 507
    async with factory() as session:
        assert (await session.execute(select(SharedVideo))).scalars().all() == []
    assert list((tmp_path / "app-videos").glob("*.mp4")) == []
