import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dossier import build as engine_build
from services.render_farm import http as farm_http
from services.render_farm.queue import RenderQueue

def _saying(version):

    async def local(*_args, **_kwargs):
        return version

    return local

class TestAtTheClaim:
    @pytest_asyncio.fixture
    async def farm(self, monkeypatch, tmp_path):
        monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "s3cret")
        queue = RenderQueue()
        replay = tmp_path / "replay.osr"
        replay.write_bytes(b"osr-bytes")
        queue.offer(str(replay), "a map", {"size": "1280x720", "fps": 60})
        app = web.Application()
        app.add_routes(farm_http.make_routes(queue))
        client = TestClient(TestServer(app))
        await client.start_server()
        yield client, queue
        await client.close()

    @staticmethod
    async def _claim(client, engine):
        return await client.post(
            "/render/claim",
            headers={"Authorization": "Bearer s3cret", "X-Render-Worker": "mac"},
            json={"engine": engine},
        )

    async def test_a_worker_on_the_same_build_is_given_the_job(self, farm, monkeypatch):
        client, _ = farm
        monkeypatch.setattr(farm_http.engine_build, "local", _saying("d 0.1.0 (abc1234)"))
        assert (await self._claim(client, "d 0.1.0 (abc1234)")).status == 200

    async def test_a_worker_on_another_build_is_turned_away(self, farm, monkeypatch):
        client, queue = farm
        monkeypatch.setattr(farm_http.engine_build, "local", _saying("d 0.1.0 (abc1234)"))
        reply = await self._claim(client, "d 0.1.0 (def5678)")
        assert reply.status == 409
        assert "def5678" in (await reply.json())["reason"]
        assert len(queue.waiting()) == 1

    async def test_an_engine_that_cannot_say_is_still_given_work(self, farm, monkeypatch):
        client, _ = farm
        monkeypatch.setattr(farm_http.engine_build, "local", _saying(None))
        assert (await self._claim(client, "d 0.1.0 (def5678)")).status == 200

class TestSayingWhichRelease:

    @pytest_asyncio.fixture
    async def farm(self, monkeypatch, tmp_path):
        monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "s3cret")
        queue = RenderQueue()
        replay = tmp_path / "replay.osr"
        replay.write_bytes(b"osr-bytes")
        queue.offer(str(replay), "a map", {"size": "1280x720", "fps": 60})
        app = web.Application()
        app.add_routes(farm_http.make_routes(queue))
        client = TestClient(TestServer(app))
        await client.start_server()
        yield client, queue
        await client.close()

    async def test_the_greeting_says_which_release(self, farm, monkeypatch):
        client, _ = farm
        monkeypatch.setattr(farm_http.engine_build, "local", _saying("d 0.1.0 (abc1234)"))
        monkeypatch.setattr(farm_http, "_release_cache", "v1.2.3")
        said = await (await client.get(
            "/render/hello?engine=d+0.1.0+(abc1234)",
            headers={"Authorization": "Bearer s3cret", "X-Render-Worker": "mac"},
        )).json()
        assert said["release"] == "v1.2.3"

    async def test_the_refusal_says_it_too(self, farm, monkeypatch):
        client, _ = farm
        monkeypatch.setattr(farm_http.engine_build, "local", _saying("d 0.1.0 (abc1234)"))
        monkeypatch.setattr(farm_http, "_release_cache", "v1.2.3")
        reply = await self._claim(client, "d 0.1.0 (def5678)")
        assert reply.status == 409
        assert (await reply.json())["release"] == "v1.2.3"

    @staticmethod
    async def _claim(client, engine):
        return await client.post(
            "/render/claim",
            headers={"Authorization": "Bearer s3cret", "X-Render-Worker": "mac"},
            json={"engine": engine},
        )

    def test_it_comes_from_the_line_pip_reads(self, monkeypatch):
        monkeypatch.setattr(farm_http, "_release_cache", None)
        from scripts.engine import wanted_tag

        assert farm_http._release() == wanted_tag()

    def test_a_bot_that_cannot_tell_says_nothing_rather_than_guessing(
        self, monkeypatch
    ):
        monkeypatch.setattr(farm_http, "_release_cache", None)
        import scripts.engine

        def broken():
            raise RuntimeError("no requirements.txt today")

        monkeypatch.setattr(scripts.engine, "wanted_tag", broken)
        assert farm_http._release() == ""
