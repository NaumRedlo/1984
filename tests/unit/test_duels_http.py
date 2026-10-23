import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.player import Player
from services.duels import http as duel_http
from services.duels import live, service, states

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'http.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add_all([Player(id=1, osu_user_id=101, osu_username="one"), Player(id=2, osu_user_id=102, osu_username="two"),
                         Player(id=3, osu_user_id=103, osu_username="three")])
        await session.commit()
    yield made
    await engine.dispose()

async def _guard(request):
    if request.headers.get("Authorization") is None:
        return web.json_response({"error": "unauthorised"}, status=401)
    return None

async def _who(request):
    said = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if said == "unlinked":
        return SimpleNamespace(telegram_id=9, player_id=None)
    return SimpleNamespace(telegram_id=int(said), player_id=int(said)) if said.isdigit() else None

def _as(player, key=None):
    headers = {"Authorization": f"Bearer {player}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers

@pytest_asyncio.fixture
async def client(factory, monkeypatch):
    hub = live.Hub()
    lost = []

    async def lose(made, duel_id, player_id):
        lost.append((duel_id, player_id))

    monkeypatch.setattr(live, "hub", hub)
    app = web.Application()
    routes = duel_http.routes(_guard, _who, lambda: factory)
    routes[0] = live.socket_route(_guard, _who, lambda: factory, lambda: hub, lose)
    app.add_routes(routes)
    async with TestClient(TestServer(app)) as made:
        made.hub, made.lost = hub, lost
        yield made

async def test_a_challenge_is_sent_seen_and_accepted_over_http(client):
    sent = await client.post("/render/duels/invitations", json={"opponent_id": 2}, headers=_as(1, "invite-0001"))
    assert sent.status == 201
    duel = await sent.json()
    again = await client.post("/render/duels/invitations", json={"opponent_id": 2}, headers=_as(1, "invite-0001"))
    assert (await again.json())["id"] == duel["id"]
    listed = await (await client.get("/render/duels", headers=_as(2))).json()
    assert [d["id"] for d in listed["duels"]] == [duel["id"]]
    seen = await (await client.get(f"/render/duels/{duel['id']}", headers=_as(2))).json()
    assert seen["actions"] == ["accept", "decline"] and seen["pool"] is None
    taken = await client.post(f"/render/duels/{duel['id']}/accept", json={"expected_version": 1}, headers=_as(2, "accept-0001"))
    assert taken.status == 200 and (await taken.json())["state"] == states.ACCEPTED
    events = await (await client.get(f"/render/duels/{duel['id']}/events?after=1", headers=_as(1))).json()
    assert [e["sequence"] for e in events["events"]] == [2]

async def test_refusals_carry_a_status_a_code_and_the_current_state(client):
    duel = await (await client.post("/render/duels/invitations", json={"opponent_id": 2}, headers=_as(1, "invite-0002"))).json()
    stale = await client.post(f"/render/duels/{duel['id']}/accept", json={"expected_version": 7}, headers=_as(2, "accept-0002"))
    body = await stale.json()
    assert stale.status == 409 and body["error"] == "VERSION_CONFLICT" and body["duel"]["version"] == 1 and body["retryable"]
    stranger = await client.get(f"/render/duels/{duel['id']}", headers=_as(3))
    assert stranger.status == 403 and (await stranger.json())["error"] == "NOT_PARTICIPANT"
    assert (await client.get("/render/duels/missing", headers=_as(1))).status == 404
    assert (await client.post("/render/duels/invitations", json={"opponent_id": 1}, headers=_as(1, "self-00001"))).status == 422
    assert (await client.post("/render/duels/invitations", json={"opponent_id": 2}, headers=_as(1))).status == 400
    assert (await client.post("/render/duels/invitations", json={"opponent_id": "2"}, headers=_as(1, "bad-000001"))).status == 400
    assert (await client.get("/render/duels", headers=_as("unlinked"))).status == 403
    assert (await client.get("/render/duels")).status == 401

async def test_the_queue_is_entered_and_left_over_http(client):
    entered = await client.post("/render/duels/queue", headers=_as(1, "queue-0001"))
    assert (await entered.json())["queue"]["status"] == "waiting"
    busy = await client.post("/render/duels/queue", headers=_as(1, "queue-0002"))
    assert busy.status == 409
    left = await client.delete("/render/duels/queue", headers=_as(1, "queue-0003"))
    assert (await left.json()) == {"queue": None}

async def test_surrender_before_play_cancels_and_while_invited_is_refused(client):
    duel = await (await client.post("/render/duels/invitations", json={"opponent_id": 2}, headers=_as(1, "invite-0003"))).json()
    early = await client.post(f"/render/duels/{duel['id']}/surrender", headers=_as(1, "give-00001"))
    assert early.status == 409
    await client.post(f"/render/duels/{duel['id']}/accept", headers=_as(2, "accept-0003"))
    done = await client.post(f"/render/duels/{duel['id']}/surrender", headers=_as(1, "give-00002"))
    assert (await done.json())["state"] == states.CANCELLED

async def test_the_stream_pushes_events_relays_frames_and_hears_witness(client, factory):
    duel = await (await client.post("/render/duels/invitations", json={"opponent_id": 2}, headers=_as(1, "invite-0004"))).json()
    await client.hub.pump(factory)
    one = await client.ws_connect("/render/duels/live", headers=_as(1))
    two = await client.ws_connect("/render/duels/live", headers=_as(2))
    viewer = await client.ws_connect("/render/duels/live", headers=_as(3))
    for socket in (one, two, viewer):
        await socket.send_json({"type": "watch", "duel": duel["id"]})
        assert (await socket.receive_json(timeout=2))["type"] == "watching"
    await client.post(f"/render/duels/{duel['id']}/accept", headers=_as(2, "accept-0004"))
    assert await client.hub.pump(factory) == 1
    for socket in (one, two, viewer):
        event = await socket.receive_json(timeout=2)
        assert (event["type"], event["event"], event["sequence"], event["payload"]["to"]) == ("event", "state", 2, states.ACCEPTED)

    frames = [[1000, 256.0, 192.0, 1], [1016, 260.5, 190.0, 0]]
    await one.send_json({"type": "frames", "duel": duel["id"], "round": 1, "frames": frames})
    for socket in (two, viewer):
        relayed = await socket.receive_json(timeout=2)
        assert (relayed["type"], relayed["player"], relayed["frames"]) == ("frames", 1, frames)
    await one.send_json({"type": "frames", "duel": duel["id"], "frames": [[1, 2, 3]]})
    await viewer.send_json({"type": "frames", "duel": duel["id"], "frames": frames})
    assert (await viewer.receive_json(timeout=2))["error"] == "NOT_PARTICIPANT"

    await two.send_json({"type": "witness", "duel": duel["id"], "state": {"score": 1200, "combo": 30, "secret": "x"}, "event": "quit"})
    shown = await one.receive_json(timeout=2)
    assert shown["state"] == {"score": 1200, "combo": 30}
    await asyncio.sleep(0.05)
    assert client.lost == [(duel["id"], 2)]
    await one.send_json({"type": "heartbeat"})
    assert (await one.receive_json(timeout=2))["type"] == "heartbeat"
    for socket in (one, two, viewer):
        await socket.close()

def test_frames_and_witness_states_are_checked_before_relaying():
    assert live.frames_ok([[0, 1.5, 2.5, 3]])
    assert not live.frames_ok([]) and not live.frames_ok([[0, 1, 2]]) and not live.frames_ok([[0, float("nan"), 2, 3]])
    assert not live.frames_ok([[0, 5000, 2, 3]]) and not live.frames_ok([[0, 1, 2, 64]]) and not live.frames_ok([[0, True, 2, 3]])
    assert not live.frames_ok([[0, 1, 2, 3]] * (live.FRAMES_MOST + 1))
    assert live.witness_state({"score": "lots"}) is None
    assert live.witness_state({"beatmap_id": 5, "mods": ["HD"], "other": 1}) == {"beatmap_id": 5, "mods": ["HD"]}

async def test_presence_loses_a_silent_player_once_and_brings_them_back(factory):
    now = [0.0]
    hub = live.Hub(clock=lambda: now[0])
    duel = await service.invite(factory, 1, 2, "invite-presence")
    await service.accept(factory, duel["id"], 2, "accept-presence")
    async with factory() as session:
        from db.models.duel import Duel
        row = await session.get(Duel, duel["id"])
        row.state = states.DRAFTING
        await session.commit()
    calls = []

    async def lose(made, duel_id, player_id):
        calls.append(("lost", player_id))
        async with made() as session:
            from db.models.duel import Duel
            row = await session.get(Duel, duel_id)
            row.state = states.PAUSED
            await session.commit()

    async def back(made, duel_id, player_id):
        calls.append(("back", player_id))

    hub.heard(1)
    hub.heard(2)
    now[0] = 10.0
    hub.heard(1)
    await hub.watch_presence(factory, lose, back)
    assert calls == []
    now[0] = 25.0
    hub.heard(1)
    await hub.watch_presence(factory, lose, back)
    await hub.watch_presence(factory, lose, back)
    assert calls == [("lost", 2)]
    hub.heard(2)
    await hub.watch_presence(factory, lose, back)
    assert calls == [("lost", 2), ("back", 2)]
