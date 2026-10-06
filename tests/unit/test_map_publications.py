import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.player import Player
from services.render_farm import publications
from services.render_farm.invites import Owner


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(publications, "AsyncSessionFactory", factory)
    async with factory() as session:
        session.add_all([Player(id=1, osu_user_id=100, osu_username="Mapper One"), Player(id=2, osu_user_id=200, osu_username="Other")])
        await session.commit()
    async def guard(request):
        return None
    async def who(request):
        said = request.headers.get("X-Test-Owner")
        return Owner(0, "Player", int(said)) if said else None
    app = web.Application()
    app.add_routes(publications.routes(guard, who))
    async with TestClient(TestServer(app)) as test:
        yield test
    await engine.dispose()


def pool():
    return {"format": "dossier-pool", "version": 1, "name": "Cup", "frame": "Free", "calc": 2, "authors": ["mapper one"], "slots": [{"hash": "a" * 32, "mods": "Nm", "category": "AB", "colour": [94, 194, 208]}]}


async def test_pool_publication_owner_revisions_and_author_links(client):
    path = "/render/catalog/pool/draft-1"
    headers = {"X-Test-Owner": "1"}
    content = pool()
    content["categories"] = {"AB": [94, 194, 208]}
    response = await client.put(path, json={"content": content, "revision": 0}, headers=headers)
    assert response.status == 200
    published = await response.json()
    assert published["name"] == "Cup"
    assert published["content"]["name"] == "Cup"
    assert published["revision"] == 1
    assert published["authors"] == [{"name": "Mapper One", "player_id": 1, "osu_id": 100}]
    retry = await client.put(path, json={"content": content, "revision": 0}, headers=headers)
    assert (await retry.json())["id"] == published["id"]
    content["name"] = "Updated"
    assert (await client.put(path, json={"content": content, "revision": 0}, headers=headers)).status == 409
    updated = await client.put(path, json={"content": content, "revision": 1}, headers=headers)
    assert (await updated.json())["revision"] == 2
    other = await client.get("/render/catalog/pool", headers={"X-Test-Owner": "2"})
    assert (await other.json())[0]["mine"] is False
    await client.delete(path, headers={"X-Test-Owner": "2"})
    assert len(await (await client.get("/render/catalog/pool", headers=headers)).json()) == 1
    foreign = await client.put(path, json={"content": content, "revision": 2}, headers={"X-Test-Owner": "2"})
    assert foreign.status == 409
    await client.delete(path, headers=headers)
    assert await (await client.get("/render/catalog/pool", headers=headers)).json() == []


async def test_collections_are_separate_editable_and_require_sign_in(client):
    data = {"name": "Favorites", "hashes": ["b" * 32]}
    path = "/render/catalog/collection/favs"
    assert (await client.put(path, json={"content": data, "revision": 0})).status == 401
    headers = {"X-Test-Owner": "1"}
    assert (await client.put(path, json={"content": data, "revision": 0}, headers=headers)).status == 200
    assert await (await client.get("/render/catalog/pool", headers=headers)).json() == []
    data["hashes"].append("c" * 32)
    result = await client.put(path, json={"content": data, "revision": 1}, headers=headers)
    assert (await result.json())["content"]["hashes"] == data["hashes"]
    data["hashes"] = ["not a hash"]
    assert (await client.put(path, json={"content": data, "revision": 2}, headers=headers)).status == 400


@pytest.mark.parametrize("content", [{}, {"name": "x", "hashes": []}, {"name": "x", "hashes": [3]}, {"name": "x" * 201, "hashes": ["a" * 32]}])
def test_invalid_collections_are_rejected(content):
    with pytest.raises(ValueError):
        publications.clean("collection", content)
