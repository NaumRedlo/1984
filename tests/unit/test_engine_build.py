import re
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.dossier import build as engine_build
from services.render_farm import http as farm_http
from services.render_farm.queue import RenderQueue

REPO = Path(__file__).resolve().parents[2]


def _saying(version):
    """A stand-in for the local engine that answers one fixed line."""

    async def local(*_args, **_kwargs):
        return version

    return local


class TestReadingTheStamp:
    def test_the_id_is_taken_out_of_the_line_the_engine_prints(self):
        assert engine_build.build_of("dossier 0.1.0 (15abdf1)") == "15abdf1"

    def test_a_build_from_an_edited_tree_keeps_its_mark(self):
        assert engine_build.build_of("dossier 0.1.0 (15abdf1+)") == "15abdf1+"
        allowed, _ = engine_build.agree("d 0.1.0 (15abdf1+)", "d 0.1.0 (15abdf1)")
        assert not allowed

    def test_two_edited_trees_are_cannot_tell_rather_than_a_refusal(self):
        # Same reasoning as two `unknown`s below: neither can say what it is,
        # so this is ignorance rather than disagreement.
        allowed, why = engine_build.agree("d 0.1.0 (15abdf1+)", "d 0.1.0 (15abdf1+)")
        assert allowed and "edited tree" in why

    @pytest.mark.parametrize("line", [None, "", "dossier 0.1.0", "dossier ("])
    def test_anything_unreadable_is_unknown_rather_than_a_guess(self, line):
        assert engine_build.build_of(line) == engine_build.UNKNOWN


class TestWhatTheStampCovers:
    """The farm once stopped because the stamp was the repository's commit.

    `drejk-starsij.local` was refused with "the bot renders with 8aae009 and
    this worker with 6054b39", and the whole difference between those two
    commits was one markdown file — two identical programs, and the work went
    back to the bot. The inputs are read out of `build.rs` rather than repeated
    here, so this test cannot drift from what actually gets stamped.
    """

    @staticmethod
    def _inputs():
        source = (REPO / "dossier/crates/dossier-cli/build.rs").read_text()
        declared = re.search(r"const INPUTS: \[&str; \d+\] = \[(.*?)\];", source, re.S)
        assert declared, "build.rs no longer declares INPUTS"
        return re.findall(r'"([^"]+)"', declared.group(1))

    @staticmethod
    def _git(*args):
        done = subprocess.run(
            ("git", *args), cwd=REPO, capture_output=True, text=True, check=False
        )
        return done.stdout.strip() if done.returncode == 0 else None

    def test_the_documents_are_not_among_them(self):
        assert "docs" not in self._inputs()
        assert "crates" in self._inputs()

    def test_a_commit_that_only_touched_documents_does_not_move_the_stamp(self):
        """Walked over real history, because that is where the bug came from.

        A synthetic pair of commits would only prove the rule this test already
        knows. The repository's own documentation commits are the thing that
        stopped the farm, so they are what gets checked.
        """
        history = self._git("log", "--format=%H", "-40")
        if not history:
            pytest.skip("no git history to read")

        checked = 0
        for commit in history.split():
            parent = self._git("rev-parse", f"{commit}^")
            if parent is None:
                continue
            touched = self._git("diff", "--name-only", parent, commit) or ""
            if not touched or not all(f.endswith(".md") for f in touched.split()):
                continue
            before, after = (
                [self._git("rev-parse", f"{rev}:dossier/{i}") for i in self._inputs()]
                for rev in (parent, commit)
            )
            assert before == after, f"{commit[:7]} moved the stamp with only documents"
            checked += 1

        if not checked:
            pytest.skip("no documents-only commit in the last 40")


class TestDeciding:
    def test_two_of_the_same_build_may_work_together(self):
        allowed, why = engine_build.agree("d 0.1.0 (abc1234)", "d 0.1.0 (abc1234)")
        assert allowed and "abc1234" in why

    def test_two_different_builds_may_not(self):
        allowed, why = engine_build.agree("d 0.1.0 (abc1234)", "d 0.1.0 (def5678)")
        assert not allowed
        assert "abc1234" in why and "def5678" in why

    def test_a_build_that_cannot_say_what_it_is_is_let_through(self):
        allowed, why = engine_build.agree(None, "d 0.1.0 (abc1234)")
        assert allowed and "cannot say" in why
        allowed, _ = engine_build.agree("d 0.1.0 (abc1234)", None)
        assert allowed


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
