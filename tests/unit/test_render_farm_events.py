import asyncio

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.render_farm import events, invites

def _as(token):
    return {"Authorization": f"Bearer {token}"}

async def _guard(request):
    if request.headers.get("Authorization") is None:
        return web.json_response({"error": "unauthorised"}, status=401)
    return None

@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(invites, "_good", set())
    monkeypatch.setattr(invites, "_owners", {})
    monkeypatch.setattr(invites, "_seen", {})
    invites.remember("one", invites.Owner(telegram_id=11, name="one", player_id=1))
    invites.remember("two", invites.Owner(telegram_id=12, name="two", player_id=2))
    invites.remember("bare", invites.Owner(telegram_id=13, name="bare", player_id=None))
    hub = events.Events()
    app = web.Application()
    app.add_routes([events.socket_route(_guard, lambda: hub, beat_every=0.05)])
    async with TestClient(TestServer(app)) as made:
        made.hub = hub
        yield made

async def _next(socket, kind):
    for _ in range(20):
        said = await asyncio.wait_for(socket.receive_json(), 2)
        if said["type"] == kind:
            return said
    raise AssertionError(f"no {kind}")

async def test_a_socket_is_greeted_with_the_topics_and_counts_its_owner_as_online(client):
    assert (await client.get("/render/events")).status == 401
    assert (await client.get("/render/events", headers=_as("stranger"))).status == 404
    one = await client.ws_connect("/render/events", headers=_as("one"))
    hello = await _next(one, "hello")
    assert hello["topics"] == ["duels", "live", "online"] and hello["online"] == 1
    two = await client.ws_connect("/render/events", headers=_as("two"))
    assert (await _next(two, "hello"))["online"] == 2
    assert (await _next(one, "online"))["online"] == 2, "the first socket hears that someone came"
    await two.close()
    invites._seen.pop(invites.digest("two"), None)
    assert (await _next(one, "online"))["online"] == 1
    await one.close()

async def test_a_change_reaches_only_the_players_it_is_for_and_a_public_one_reaches_everyone(client):
    one = await client.ws_connect("/render/events", headers=_as("one"))
    two = await client.ws_connect("/render/events", headers=_as("two"))
    bare = await client.ws_connect("/render/events", headers=_as("bare"))
    for socket in (one, two, bare):
        await _next(socket, "hello")
    assert await client.hub.tell({1}, "duels") == 1
    assert await _next(one, "changed") == {"type": "changed", "topic": "duels"}
    assert await client.hub.tell(None, "live") == 3
    for socket in (one, two, bare):
        assert (await _next(socket, "changed"))["topic"] == "live"
    assert client.hub.connected(1) and not client.hub.connected(7)
    await one.close()
    for _ in range(50):
        if not client.hub.connected(1):
            break
        await asyncio.sleep(0.02)
    assert not client.hub.connected(1)
    assert await client.hub.tell({1}, "duels") == 0
    await two.close()
    await bare.close()

async def test_one_device_keeps_a_few_sockets_at_most(client):
    held = [await client.ws_connect("/render/events", headers=_as("one")) for _ in range(events.SOCKETS_MOST + 2)]
    for _ in range(50):
        if sum(1 for socket, (token, _) in client.hub.sockets.items() if token == "one" and not socket.closed) <= events.SOCKETS_MOST:
            break
        await asyncio.sleep(0.02)
    assert sum(1 for socket, (token, _) in client.hub.sockets.items() if token == "one" and not socket.closed) <= events.SOCKETS_MOST
    for socket in held:
        await socket.close()
