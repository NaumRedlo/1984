"""_check_gl_ready (utils/osu/danser_renderer) — the 2026-07-03 fix for a
render right after a GPU wake hitting danser's GL-context startup deadlock.
The worker's old /health only confirmed the Python process was listening,
which happens well before Xorg's NVIDIA driver stack has actually settled
enough to hand out a GLX context. glxinfo does a real GLX context
creation+query, the same operation danser's own startup needs."""

import asyncio

import pytest

from utils.osu import danser_renderer as dr


class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode

    async def wait(self):
        return self.returncode


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(dr.core, "_gl_ready_confirmed", False)
    monkeypatch.setattr(dr.core, "_glxinfo_missing_warned", False)
    monkeypatch.setattr(dr.core, "RENDER_GPU", True)


async def test_skips_the_probe_entirely_on_cpu_mode(monkeypatch):
    monkeypatch.setattr(dr.core, "RENDER_GPU", False)
    calls = []

    async def fake_exec(*a, **kw):
        calls.append(a)
        return _FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert await dr._check_gl_ready() is True
    assert calls == []  # never even ran glxinfo — CPU path doesn't need GLX


async def test_true_and_cached_on_success(monkeypatch):
    calls = []

    async def fake_exec(*a, **kw):
        calls.append(a)
        return _FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert await dr._check_gl_ready() is True
    assert await dr._check_gl_ready() is True
    assert len(calls) == 1  # second call served from the cached flag


async def test_false_and_not_cached_on_nonzero_exit(monkeypatch):
    calls = []

    async def fake_exec(*a, **kw):
        calls.append(a)
        return _FakeProc(1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert await dr._check_gl_ready() is False
    assert await dr._check_gl_ready() is False
    assert len(calls) == 2  # not cached — a real failure keeps being re-probed


async def test_false_on_timeout(monkeypatch):
    async def fake_exec(*a, **kw):
        return _FakeProc(0)

    async def fake_wait_for(coro, timeout):
        coro.close()  # avoid a "coroutine was never awaited" warning
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    assert await dr._check_gl_ready() is False


async def test_missing_glxinfo_degrades_to_ready_and_warns_once(monkeypatch):
    async def fake_exec(*a, **kw):
        raise FileNotFoundError("glxinfo not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    warnings = []
    monkeypatch.setattr(dr.core.logger, "warning", lambda msg: warnings.append(msg))

    assert await dr._check_gl_ready() is True
    assert await dr._check_gl_ready() is True  # cached now, no second warning
    assert len(warnings) == 1
