"""render_replay()'s single retry for the GL-context startup race observed on
the render worker 2026-07-02: danser occasionally deadlocks in its main GL
goroutine right after the worker process (re)starts, recovered by a bare
retry. Must NOT retry on a genuine failure with different output."""

import asyncio

import pytest

from utils.osu import danser_renderer as dr


class _FakeStdout:
    def __init__(self, lines):
        self._lines = [line.encode() for line in lines] + [b""]

    async def readline(self):
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, returncode, lines):
        self.returncode = returncode
        self.stdout = _FakeStdout(lines)

    async def wait(self):
        return self.returncode


def _patch_common(monkeypatch, tmp_path):
    monkeypatch.setattr(dr.core, "_check_danser", lambda: str(tmp_path / "danser-cli"))
    monkeypatch.setattr(dr.core, "_build_spatch", lambda settings=None: "{}")
    monkeypatch.setattr(dr.core, "RENDER_GPU", False)  # avoid touching real DISPLAY env


async def test_retries_once_on_the_known_startup_race(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path)
    # render_replay looks for <danser_dir>/videos/<out_name>.mp4 on success.
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    (videos_dir / "out.mp4").write_bytes(b"fake mp4 bytes")
    attempts = []

    async def fake_exec(*cmd, **kwargs):
        attempts.append(cmd)
        if len(attempts) == 1:
            return _FakeProc(1, [
                "2026/07/02 08:27:14 goroutine 2 [chan receive, locked to thread]:",
                "2026/07/02 08:27:14 github.com/wieku/danser-go/app.run()",
            ])
        return _FakeProc(0, ["Progress: 100%"])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    sleeps = []

    async def fake_sleep(secs):
        sleeps.append(secs)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    out = str(tmp_path / "out")
    result = await dr.render_replay(str(tmp_path / "replay.osr"), out)

    assert result.endswith(".mp4")
    assert len(attempts) == 2          # retried exactly once
    assert sleeps == [1.5]             # the short backoff before the retry


async def test_does_not_retry_a_genuine_failure(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path)
    attempts = []

    async def fake_exec(*cmd, **kwargs):
        attempts.append(cmd)
        return _FakeProc(1, ["panic: beatmap not found"])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(dr.DanserError):
        await dr.render_replay(str(tmp_path / "replay.osr"), str(tmp_path / "out"))

    assert len(attempts) == 1          # no retry for an unrelated failure
