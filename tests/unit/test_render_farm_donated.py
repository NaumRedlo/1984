import struct
import types

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.render_farm import donated, http, invites

def _replay(tail: bytes = b"") -> bytes:
    md5 = b"5b73773f55e18a9756dd3261938aa454"
    return bytes([0]) + struct.pack("<i", 20260711) + b"\x0b" + bytes([len(md5)]) + md5 + tail

class _Bot:
    async def get_chat_member(self, chat_id, user_id):
        return types.SimpleNamespace(status="member")

@pytest_asyncio.fixture
async def served(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "RENDER_WORKER_TOKEN", "a-shared-secret")
    monkeypatch.setattr(donated, "DONATED_REPLAYS_DIR", str(tmp_path / "donated"))
    token = "a-personal-token-of-sixty-four-characters-more-or-less-long-z"
    invites.remember(token, invites.Owner(7, "Naum"))
    http.set_bot(_Bot())
    app = web.Application()
    app.add_routes(http.make_routes())
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, {"Authorization": f"Bearer {token}", "X-Render-Worker": "mac"}, tmp_path / "donated"
    await client.close()
    http.set_bot(None)
    invites.forget(invites.digest(token))

async def test_a_donated_replay_is_kept_once_under_its_hash(served):
    client, mine, folder = served
    first = await client.post("/render/me/replay", data=_replay(b"frames"), headers=mine)
    assert first.status == 200 and (await first.json()) == {"ok": True, "known": False}
    again = await client.post("/render/me/replay", data=_replay(b"frames"), headers=mine)
    assert (await again.json())["known"] is True
    kept = sorted(p.name for p in folder.iterdir())
    assert len([name for name in kept if name.endswith(".osr")]) == 1
    index = (folder / donated.INDEX).read_text().splitlines()
    assert len(index) == 1 and index[0].split("\t")[1] == "7"

async def test_what_is_not_a_replay_is_turned_away(served):
    client, mine, folder = served
    reply = await client.post("/render/me/replay", data=b"GIF89a not a replay", headers=mine)
    assert reply.status == 400
    assert not folder.exists() or not any(folder.iterdir())

async def test_a_stranger_cannot_donate(served):
    client, _, _ = served
    reply = await client.post("/render/me/replay", data=_replay(), headers={"X-Render-Worker": "mac"})
    assert reply.status == 401

async def test_a_replay_too_large_is_refused(served, monkeypatch):
    client, mine, _ = served
    monkeypatch.setattr(http, "RENDER_REPLAY_MOST", 64)
    reply = await client.post("/render/me/replay", data=_replay(b"x" * 200), headers=mine)
    assert reply.status == 413

async def test_a_full_store_says_so(served, monkeypatch):
    client, mine, _ = served
    monkeypatch.setattr(donated, "DONATED_REPLAYS_STORAGE_MOST", 10)
    reply = await client.post("/render/me/replay", data=_replay(b"frames"), headers=mine)
    assert reply.status == 507

def test_the_header_is_read_the_way_osu_writes_it():
    assert donated.looks_like_replay(_replay())
    assert not donated.looks_like_replay(b"\x07" + _replay()[1:])
    assert not donated.looks_like_replay(bytes([0]) + struct.pack("<i", 5) + b"\x0b")
    assert not donated.looks_like_replay(b"\x00\x01")
