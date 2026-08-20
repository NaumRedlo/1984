"""A worker saying which build of the engine it is about to render with.

The failure this prevents leaves no trace: a worker whose binary is behind the
bot's renders with old code, the output looks plausible, and nobody finds out
until they notice the pictures are wrong. It is the same shape as a skin folder
unpacked by an importer since fixed — stale input rather than broken input,
and stale does not announce itself.
"""

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.dossier import build as engine_build
from services.render_farm import http as farm_http
from services.render_farm.queue import RenderQueue


def _saying(version):
    """A stand-in for the local engine that answers one fixed line."""

    async def local(*_args, **_kwargs):
        return version

    return local


class TestReadingTheStamp:
    def test_the_commit_is_taken_out_of_the_line_the_engine_prints(self):
        assert engine_build.commit_of("dossier 0.1.0 (15abdf1)") == "15abdf1"

    def test_a_build_from_an_edited_tree_keeps_its_mark(self):
        # The `+` is not decoration. A build from an edited tree is not the
        # commit it names, so it must not compare equal to one that is.
        assert engine_build.commit_of("dossier 0.1.0 (15abdf1+)") == "15abdf1+"
        allowed, _ = engine_build.agree("d 0.1.0 (15abdf1+)", "d 0.1.0 (15abdf1)")
        assert not allowed

    @pytest.mark.parametrize("line", [None, "", "dossier 0.1.0", "dossier ("])
    def test_anything_unreadable_is_unknown_rather_than_a_guess(self, line):
        assert engine_build.commit_of(line) == engine_build.UNKNOWN


class TestDeciding:
    def test_two_of_the_same_build_may_work_together(self):
        allowed, why = engine_build.agree("d 0.1.0 (abc1234)", "d 0.1.0 (abc1234)")
        assert allowed and "abc1234" in why

    def test_two_different_builds_may_not(self):
        allowed, why = engine_build.agree("d 0.1.0 (abc1234)", "d 0.1.0 (def5678)")
        assert not allowed
        # The reason says which is which, because whoever reads it has to know
        # which machine to rebuild.
        assert "abc1234" in why and "def5678" in why

    def test_a_build_that_cannot_say_what_it_is_is_let_through(self):
        # Two `unknown`s are not thereby the same, and a farm that stops because
        # somebody built from a tarball has failed at something that was never
        # its business. Let through, and the reason says why it could not tell.
        allowed, why = engine_build.agree(None, "d 0.1.0 (abc1234)")
        assert allowed and "cannot say" in why
        allowed, _ = engine_build.agree("d 0.1.0 (abc1234)", None)
        assert allowed


class TestAtTheClaim:
    """The check where it actually runs: a worker asking for a job."""

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
        # And the job is still there for the bot to render itself, which is the
        # whole point of refusing rather than letting it through.
        assert len(queue.waiting()) == 1

    async def test_an_engine_that_cannot_say_is_still_given_work(self, farm, monkeypatch):
        client, _ = farm
        monkeypatch.setattr(farm_http.engine_build, "local", _saying(None))
        assert (await self._claim(client, "d 0.1.0 (def5678)")).status == 200
