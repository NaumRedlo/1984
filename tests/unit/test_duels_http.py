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
    assert sorted(p["name"] for p in seen["participants"]) == ["one", "two"]
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
    assert busy.status == 200
    assert (await busy.json())["queue"] == (await entered.json())["queue"]
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

async def test_my_duel_profile_and_the_history_are_served(client):
    mine = await (await client.get("/render/duels/profile/me", headers=_as(1))).json()
    assert mine["rating"]["calibrating"] and mine["rating"]["needed"] == 5
    assert mine["portfolio"]["slots"]["NM1"]["mark"] == "PRIOR" and mine["observations"] == 0
    assert (mine["won"], mine["lost"]) == (0, 0)
    history = await (await client.get("/render/duels/history?player=2", headers=_as(1))).json()
    assert history == {"duels": []}
    assert (await client.get("/render/duels/history?player=x", headers=_as(1))).status == 400

async def test_the_live_list_my_roles_and_the_balance_desk_are_served(client, factory):
    from db.models.duel import DuelRole

    duel = await (await client.post("/render/duels/invitations", json={"opponent_id": 2}, headers=_as(1, "invite-live1"))).json()
    before = await (await client.get("/render/duels/running", headers=_as(3))).json()
    assert before == {"running": [], "finished": []}
    await client.post(f"/render/duels/{duel['id']}/accept", json={"expected_version": 1}, headers=_as(2, "accept-live1"))
    running = await (await client.get("/render/duels/running", headers=_as(3))).json()
    assert [d["id"] for d in running["running"]] == [duel["id"]]
    assert sorted(p["name"] for p in running["running"][0]["players"]) == ["one", "two"] and running["running"][0]["watching"] == 0
    assert await (await client.get("/render/duels/roles/me", headers=_as(1))).json() == {"roles": []}
    for path in ("queue", "catalog", "catalog/NM1/0", "stuck", "journal", "people", "models"):
        refused = await client.get(f"/render/admin/duels/{path}", headers=_as(1))
        assert refused.status == 403, path
    async with factory() as session:
        session.add_all([DuelRole(player_id=1, role="BALANCE_REVIEWER"), DuelRole(player_id=1, role="TECHNICAL_OPERATOR")])
        await session.commit()
    assert await (await client.get("/render/duels/roles/me", headers=_as(1))).json() == {"roles": ["BALANCE_REVIEWER", "TECHNICAL_OPERATOR"]}
    assert await (await client.get("/render/admin/duels/queue", headers=_as(1))).json() == {"tasks": []}
    catalogue = await (await client.get("/render/admin/duels/catalog", headers=_as(1))).json()
    assert catalogue["totals"]["playable"] == 0 and len(catalogue["grid"]["NM1"]) == len(catalogue["bands"])
    assert (await client.get("/render/admin/duels/catalog/XX1/0", headers=_as(1))).status == 400
    assert await (await client.get("/render/admin/duels/stuck", headers=_as(1))).json() == {"duels": []}
    people = await (await client.get("/render/admin/duels/people", headers=_as(1))).json()
    assert people == {"people": [{"player": 1, "name": "one", "osu_id": 101, "roles": ["BALANCE_REVIEWER", "TECHNICAL_OPERATOR"]}]}
    assert await (await client.get("/render/admin/duels/models", headers=_as(1))).json() == {"models": []}
    assert await (await client.get("/render/admin/duels/journal", headers=_as(1))).json() == {"entries": []}

@pytest.mark.parametrize("identity,expected", [("telegram", 200), ("unlinked", 403), ("player", 200), ("deleted", 404)])
async def test_render_duel_routes_resolve_legacy_telegram_identity(factory, monkeypatch, identity, expected):
    import db.database
    import config.settings
    from services.render_farm import http as farm_http, invites

    async with factory() as session:
        player = await session.get(Player, 1)
        player.telegram_id = 700
        await session.commit()
    monkeypatch.setattr(db.database, "AsyncSessionFactory", factory)
    monkeypatch.setattr(config.settings, "ADMIN_IDS", [700])
    owners = {
        "telegram": invites.Owner(700, "old name"),
        "unlinked": invites.Owner(701, "one"),
        "player": invites.Owner(0, "old name", 1),
        "deleted": invites.Owner(700, "old name", 999),
    }
    monkeypatch.setattr(invites, "owner", lambda token: owners[identity])
    app = web.Application()
    app.add_routes(farm_http._duel_routes(_guard))
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/render/duels/roles/me", headers=_as("legacy"))
        assert response.status == expected
        if expected == 200:
            assert "OWNER" in (await response.json())["roles"]
            profile = await client.get("/render/duels/profile/me", headers=_as("legacy"))
            assert profile.status == 200
        elif expected == 403:
            assert (await response.json())["error"] == "NOT_LINKED"


async def test_balance_presence_is_authenticated_and_requires_a_role(client, factory):
    from db.models.duel import DuelRole
    path = "/render/admin/duels/presence"
    assert (await client.post(path, json={"section": "queue"})).status == 401
    assert (await client.post(path, json={"section": "queue"}, headers=_as(1))).status == 403
    async with factory() as session:
        session.add(DuelRole(player_id=1, role="BALANCE_REVIEWER"))
        await session.commit()
    response = await client.post(path, json={"section": "label", "beatmap": 123}, headers=_as(1))
    assert response.status == 200
    assert (await response.json())["people"][0]["beatmap"] == 123
    response = await client.post(path, json={"section": "catalog", "slot": "FM3", "band": 9}, headers=_as(1))
    assert response.status == 200
    person = (await response.json())["people"][0]
    assert (person["section"], person["slot"], person["band"]) == ("catalog", "FM3", 9)
    assert (await client.get("/render/admin/duels/catalog/FM3/0", headers=_as(1))).status == 200
    for body in ({"section": []}, {"section": "queue", "beatmap": True}):
        assert (await client.post(path, json=body, headers=_as(1))).status == 400
    response = await client.post(path, json={"section": None}, headers=_as(1))
    assert (await response.json())["people"] == []
    assert (await client.get("/render/admin/duels/maps/7/inspect", headers=_as(2))).status == 403
    assert (await client.get("/render/admin/duels/maps/7/inspect", headers=_as(1))).status == 404
    adding = "/render/admin/duels/catalog/maps"
    for body in ({}, {"beatmap_id": "7"}, {"beatmap_id": True}, {"beatmap_id": 0}, {"beatmap_id": 7, "slot": 2}):
        assert (await client.post(adding, json=body, headers=_as(1))).status == 400, body
    assert (await client.post(adding, json={"beatmap_id": 7}, headers=_as(2))).status == 403
    recovering = await client.post(adding, json={"beatmap_id": 7, "slot": "NM1"}, headers=_as(1))
    assert recovering.status == 503 and (await recovering.json())["error"] == "SERVICE_RECOVERING"


async def test_http_search_presence_matches_without_a_websocket(client, factory):
    from services.duels import matchmaker

    for player in (1, 2):
        response = await client.post("/render/duels/queue", headers=_as(player, f"search-{player}-0001"))
        assert response.status == 200
    assert client.hub.present(1) and client.hub.present(2)
    assert not client.hub.owners
    waiting = await (await client.get("/render/duels", headers=_as(1))).json()
    assert waiting["queue"]["status"] == "waiting"
    duel_id = await matchmaker.offer(factory, client.hub.present)
    assert duel_id is not None
    for player in (1, 2):
        snapshot = await (await client.get("/render/duels", headers=_as(player))).json()
        assert snapshot["queue"] is None
        assert snapshot["duels"][0]["id"] == duel_id
    client.hub.seen.clear()
    await client.get("/render/duels", headers=_as(1))
    assert client.hub.present(1) and not client.hub.present(2)


async def test_batches_are_managed_only_by_the_owner_with_a_named_batch(client, factory):
    from db.models.duel import DuelRole

    async with factory() as session:
        session.add(DuelRole(player_id=1, role="BALANCE_REVIEWER"))
        await session.commit()
    assert (await client.get("/render/admin/duels/corpus/batches", headers=_as(1))).status == 403
    for path in ("rename", "delete", "exclude"):
        assert (await client.post(f"/render/admin/duels/corpus/batches/{path}", json={"year": 2024}, headers=_as(1))).status == 400, path
    refused = await client.post("/render/admin/duels/corpus/batches/delete", json={"name": "Cup", "year": 2024}, headers=_as(1))
    assert refused.status == 403


async def test_a_training_run_is_followed_by_its_job_until_it_ends(client, factory, monkeypatch):
    from db.models.duel import DuelRole
    from services.duels import training
    from tests.unit.test_duels_balance import _labelled

    real = training.train
    monkeypatch.setattr(training, "train", lambda factory, steps=400, now=None, report=None: real(factory, 20, now, report))
    started = await client.post("/render/admin/duels/models/train", json={"reason": "first"}, headers=_as(1))
    assert started.status == 403
    async with factory() as session:
        session.add(DuelRole(player_id=1, role="BALANCE_REVIEWER"))
        await session.commit()
    await _labelled(factory, 20)

    async def finished(job):
        for _ in range(200):
            found = await (await client.get(f"/render/admin/duels/models/training/{job}", headers=_as(1))).json()
            if found["state"] != "running":
                return found
            await asyncio.sleep(0.05)
        raise AssertionError("the training never ended")

    said = await (await client.post("/render/admin/duels/models/train", json={"reason": "first"}, headers=_as(1))).json()
    assert said["started"] and said["job"]
    done = await finished(said["job"])
    assert (done["state"], done["version"], done["error"]) == ("done", "kamerton-1", None)
    again = await client.post("/render/admin/duels/models/train", json={"reason": "again"}, headers=_as(1))
    assert again.status == 409 and (await again.json())["error"] == "NOTHING_NEW"
    assert (await client.get("/render/admin/duels/models/training/nobody-knows", headers=_as(1))).status == 404
    assert (await client.get(f"/render/admin/duels/models/training/{said['job']}", headers=_as(2))).status == 403


async def test_discovery_requires_a_model_forwards_slot_and_resumes_search(factory, monkeypatch):
    from unittest.mock import AsyncMock
    from db.models.duel import DuelRole
    from services.duels import balance, importer, desk
    async with factory() as session:
        session.add(DuelRole(player_id=1, role="BALANCE_REVIEWER"))
        await session.commit()
    monkeypatch.setattr(balance, "working_model", AsyncMock(return_value=None))
    finished, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def discover(*args, **kwargs):
        calls.append((args, kwargs))
        await release.wait()
        kwargs["advance"](3)
        return {"known": 12, "below_threshold": 3, "proposed DT2": 2}
    monkeypatch.setattr(importer, "discover", discover)
    original = balance.discovery_result
    async def reported(*args, **kwargs):
        await original(*args, **kwargs)
        finished.set()
    monkeypatch.setattr(balance, "discovery_result", reported)
    app = web.Application()
    osu = object()
    app.add_routes(duel_http.routes(_guard, _who, lambda: factory, lambda: osu))
    async with TestClient(TestServer(app)) as client:
        path = "/render/admin/duels/catalog/discover"
        body = {"slot": "DT2", "band": 1}
        response = await client.post(path, json=body, headers=_as(1))
        assert response.status == 409 and (await response.json())["error"] == "MODEL_UNAVAILABLE"
        assert not calls
        assert (await client.post(path, json=body, headers=_as(2))).status == 403
        model = SimpleNamespace(version="published")
        monkeypatch.setattr(balance, "working_model", AsyncMock(return_value=model))
        response = await client.post(path, json=body, headers=_as(1))
        assert (await response.json()) == {"started": True}
        again = await client.post(path, json=body, headers=_as(1))
        assert (await again.json()) == {"started": False}
        release.set()
        await asyncio.wait_for(finished.wait(), 2)
        assert calls[0][0][4:7] == (5.5, 6.0, 4)
        assert calls[0][1]["slot"] == "DT2" and calls[0][1]["model"] is model
        assert calls[0][1]["page_from"] == 0
        finished.clear()
        response = await client.post(path, json=body, headers=_as(1))
        assert (await response.json()) == {"started": True}
        await asyncio.wait_for(finished.wait(), 2)
        assert calls[-1][1]["page_from"] == 3
        result = next(row for row in await desk.journal(factory, 1) if row["action"] == "discover_result")
        assert result["after"]["outcomes"]["below_threshold"] == 3


async def test_a_review_carries_the_tier_and_the_reasons_and_refuses_unknown_ones(client, factory):
    from db.models.duel import DuelRole
    from db.models.duel_catalog import DuelMapAssignment

    async with factory() as session:
        session.add(DuelRole(player_id=1, role="BALANCE_REVIEWER"))
        session.add(DuelMapAssignment(beatmap_id=7, slot="NM1", state="AUTO_ASSIGNED", revision_hash="a" * 32, admitted=True))
        await session.commit()
    path = "/render/admin/duels/maps/7/review"
    for body in ({"tier": 1}, {"reasons": "jumps"}, {"reasons": [1]}):
        assert (await client.post(path, json={"decision": "APPROVE", "reason": "", **body}, headers=_as(1))).status == 400, body
    refused = await client.post(path, json={"decision": "APPROVE", "reason": "", "reasons": ["vibes"]}, headers=_as(1))
    assert refused.status == 409 and (await refused.json())["error"] == "UNKNOWN_REASON"
    answered = await client.post(path, json={"decision": "APPROVE", "reason": "", "tier": "exemplary", "reasons": ["jumps", "turns"]}, headers=_as(1))
    assert answered.status == 200
    found = await answered.json()
    assert (found["tier"], found["reasons"]) == ("exemplary", ["jumps", "turns"])


async def test_presence_recovers_training_and_repeated_start_joins_the_same_job(client, factory, monkeypatch):
    from db.models.duel import DuelRole
    from services.duels import training
    from unittest.mock import AsyncMock

    monkeypatch.setattr(training, "jobs", {})
    monkeypatch.setattr(training, "unchanged", AsyncMock(return_value=False))
    entered, release, ended = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    async def train(factory, steps=400, now=None, report=None):
        calls.append(1)
        report(phase="fitting", completed=35, total=100, maps=20)
        entered.set()
        await release.wait()
        ended.set()
        return "kamerton-42", {}
    monkeypatch.setattr(training, "train", train)
    async with factory() as session:
        session.add(DuelRole(player_id=1, role="BALANCE_REVIEWER"))
        await session.commit()
    path = "/render/admin/duels/models/train"
    first = await (await client.post(path, json={"reason": "first"}, headers=_as(1))).json()
    await asyncio.wait_for(entered.wait(), 2)
    try:
        repeated = await (await client.post(path, json={"reason": "repeat"}, headers=_as(1))).json()
        assert repeated == {"started": False, "job": first["job"]}
        found = await (await client.post("/render/admin/duels/presence", json={"section": "model"}, headers=_as(1))).json()
        assert found["training_known"] and found["training"]["job"] == first["job"]
        assert found["training"]["completed"] == 35 and found["training"]["total"] == 100
        assert (await client.post("/render/admin/duels/presence", json={"section": "model"}, headers=_as(2))).status == 403
        release.set()
        await asyncio.wait_for(ended.wait(), 2)
        found = await (await client.post("/render/admin/duels/presence", json={"section": "model"}, headers=_as(1))).json()
        assert found["training"]["state"] == "done" and found["training"]["version"] == "kamerton-42"
        assert len(calls) == 1
    finally:
        release.set()
        await asyncio.wait_for(ended.wait(), 2)
