"""On-demand GPU power coordinator (utils/cloud/gpu_power)."""

import asyncio
from unittest.mock import patch

import pytest

from utils.cloud import gpu_power
from utils.cloud import intelion


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(gpu_power, "_active", 0)
    monkeypatch.setattr(gpu_power, "_wake_task", None)
    monkeypatch.setattr(gpu_power, "_off_task", None)
    monkeypatch.setattr(gpu_power, "_bot", None)
    monkeypatch.setattr(gpu_power, "_watchdog_snooze_until", None)
    monkeypatch.setattr(gpu_power, "_HEALTH_POLL_SECONDS", 0)
    # Immediate power-off by default in tests; the warm-window test overrides this.
    monkeypatch.setattr(gpu_power, "RENDER_WARM_SECONDS", 0)
    # No sleep between retries in tests.
    monkeypatch.setattr(gpu_power, "RENDER_POWEROFF_RETRY_SECONDS", 0)


async def test_session_noop_when_autopower_off(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", False)

    calls = []
    monkeypatch.setattr(intelion, "power_on", lambda: calls.append("on"))
    monkeypatch.setattr(intelion, "power_off", lambda: calls.append("off"))

    async with gpu_power.session():
        pass

    assert calls == []  # power API never touched


async def test_session_wakes_then_powers_off(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    calls = []

    async def fake_power_on():
        calls.append("on")

    async def fake_power_off():
        calls.append("off")

    async def fake_check():
        return {"can_start": True}

    # Not up at first; up after power_on.
    health = {"up": False}

    async def fake_health(timeout=5.0):
        return health["up"]

    async def flip_up():
        calls.append("on")
        health["up"] = True

    monkeypatch.setattr(intelion, "power_on", flip_up)
    monkeypatch.setattr(intelion, "power_off", fake_power_off)
    monkeypatch.setattr(intelion, "get_start_check", fake_check)
    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    async with gpu_power.session():
        assert health["up"] is True  # ready inside the session

    assert calls == ["on", "off"]
    assert gpu_power._active == 0


async def test_wake_checks_health_before_sleeping(monkeypatch):
    """2026-07-03: the wake loop used to sleep _HEALTH_POLL_SECONDS BEFORE its
    first check, adding dead time even when the server answers immediately.
    It now checks right after power_on() and only sleeps if that first check
    fails — so if health is already true, asyncio.sleep must never be called."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)
    monkeypatch.setattr(gpu_power, "_HEALTH_POLL_SECONDS", 5)  # would be very slow if hit

    health = {"up": False}

    async def fake_power_on():
        health["up"] = True  # ready the instant power_on returns

    async def fake_health(timeout=5.0):
        return health["up"]

    async def fake_check():
        return {"can_start": True}

    sleep_calls = []
    real_sleep = asyncio.sleep

    async def spy_sleep(secs):
        sleep_calls.append(secs)
        await real_sleep(0)

    monkeypatch.setattr(intelion, "power_on", fake_power_on)
    monkeypatch.setattr(intelion, "power_off", lambda: real_sleep(0))
    monkeypatch.setattr(intelion, "get_start_check", fake_check)
    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)
    monkeypatch.setattr(asyncio, "sleep", spy_sleep)

    async with gpu_power.session():
        pass

    assert sleep_calls == []  # never slept — the first (post-power_on) check already succeeded


async def test_session_skips_wake_when_already_up(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    calls = []
    monkeypatch.setattr(intelion, "power_on", lambda: calls.append("on"))

    async def fake_power_off():
        calls.append("off")

    async def fake_health(timeout=5.0):
        return True  # already up

    monkeypatch.setattr(intelion, "power_off", fake_power_off)
    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    async with gpu_power.session():
        pass

    assert "on" not in calls       # no power_on needed
    assert calls == ["off"]        # still powered off at the end


async def test_warm_window_keeps_server_for_next_render(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)
    monkeypatch.setattr(gpu_power, "RENDER_WARM_SECONDS", 60)  # long enough to stay pending

    calls = []
    health = {"up": False}

    async def fake_power_on():
        calls.append("on")
        health["up"] = True

    async def fake_power_off():
        calls.append("off")

    async def fake_check():
        return {"can_start": True}

    async def fake_health(timeout=5.0):
        return health["up"]

    monkeypatch.setattr(intelion, "power_on", fake_power_on)
    monkeypatch.setattr(intelion, "power_off", fake_power_off)
    monkeypatch.setattr(intelion, "get_start_check", fake_check)
    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    async with gpu_power.session():
        pass

    # Server stays warm: power_off is deferred, not called yet.
    assert calls == ["on"]
    assert gpu_power._off_task is not None and not gpu_power._off_task.done()

    # A second render within the warm window cancels the pending off and reuses
    # the warm server (no second power_on).
    async with gpu_power.session():
        pass
    assert calls == ["on"]

    if gpu_power._off_task:
        gpu_power._off_task.cancel()


async def test_can_start_false_raises(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    async def fake_check():
        return {"can_start": False}

    async def fake_health(timeout=5.0):
        return False

    monkeypatch.setattr(intelion, "get_start_check", fake_check)
    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    with pytest.raises(gpu_power.GpuPowerError):
        async with gpu_power.session():
            pass

    assert gpu_power._active == 0  # accounting balanced after failure


async def test_power_off_retries_then_succeeds(monkeypatch):
    """A transient Intelion failure must not be the end of the story — the
    2026-07-01 incident was exactly a single failed power-off left unretried."""
    monkeypatch.setattr(gpu_power, "RENDER_POWEROFF_RETRIES", 3)

    attempts = []

    async def flaky_power_off():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("Intelion API hiccup")

    monkeypatch.setattr(intelion, "power_off", flaky_power_off)

    ok = await gpu_power._power_off_with_retry("test")

    assert ok is True
    assert len(attempts) == 3


async def test_power_off_gives_up_and_alerts_admin(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_POWEROFF_RETRIES", 2)
    monkeypatch.setattr(gpu_power, "ADMIN_IDS", [111, 222])

    async def always_fails():
        raise RuntimeError("Intelion is down")

    monkeypatch.setattr(intelion, "power_off", always_fails)

    alerted = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            alerted.append(chat_id)

    gpu_power.set_bot(FakeBot())

    ok = await gpu_power._power_off_with_retry("test")

    assert ok is False
    assert alerted == [111, 222]  # every admin notified


async def test_watchdog_asks_instead_of_killing_when_idle(monkeypatch):
    """Simulates: bot thinks nothing is active/scheduled, and the worker's own
    counter agrees it's idle too. The watchdog used to force a power-off here —
    that once cut off a render the accounting had lost track of, so now it must
    ASK an admin instead of deciding unilaterally."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    async def fake_health(timeout=5.0):
        return True  # still up, unexpectedly

    async def fake_inflight(timeout=5.0):
        return 0  # worker agrees: idle

    power_calls = []
    monkeypatch.setattr(intelion, "power_off", lambda: power_calls.append("off"))
    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)
    monkeypatch.setattr(gpu_power, "_worker_inflight", fake_inflight)

    prompts = []

    async def fake_prompt():
        prompts.append(1)

    monkeypatch.setattr(gpu_power, "_prompt_admins_idle_server", fake_prompt)

    await gpu_power.watchdog_tick()

    assert power_calls == []          # never decides unilaterally anymore
    assert prompts == [1]             # asks instead


async def test_watchdog_leaves_alone_when_worker_reports_activity(monkeypatch):
    """The worker's OWN in-flight counter is real activity — even if this
    process's bookkeeping lost track, don't ask/kill a render that's genuinely
    still running."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    async def fake_health(timeout=5.0):
        return True

    async def fake_inflight(timeout=5.0):
        return 1  # worker says a render IS running

    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)
    monkeypatch.setattr(gpu_power, "_worker_inflight", fake_inflight)

    prompts = []
    monkeypatch.setattr(gpu_power, "_prompt_admins_idle_server", lambda: prompts.append(1))

    await gpu_power.watchdog_tick()

    assert prompts == []


async def test_watchdog_respects_snooze(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    async def fake_health(timeout=5.0):
        return True

    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)
    gpu_power.snooze_watchdog(3600)  # admin said "leave it on"

    calls = []
    monkeypatch.setattr(gpu_power, "_worker_inflight", lambda timeout=5.0: calls.append(1))

    await gpu_power.watchdog_tick()

    assert calls == []  # never even checked -- snoozed


async def test_force_power_off_retries_like_any_other_path(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_POWEROFF_RETRIES", 2)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("blip")

    monkeypatch.setattr(intelion, "power_off", flaky)
    assert await gpu_power.force_power_off("test") is True
    assert len(calls) == 2


async def test_watchdog_skips_when_active_or_scheduled(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    calls = []
    monkeypatch.setattr(intelion, "power_off", lambda: calls.append("off"))

    # Active render in flight — must not touch power.
    monkeypatch.setattr(gpu_power, "_active", 1)
    await gpu_power.watchdog_tick()
    assert calls == []

    # A power-off is already scheduled — must not double-fire.
    monkeypatch.setattr(gpu_power, "_active", 0)
    monkeypatch.setattr(gpu_power, "_off_task", asyncio.get_event_loop().create_future())
    await gpu_power.watchdog_tick()
    assert calls == []


async def test_watchdog_noop_when_autopower_off(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", False)

    calls = []
    monkeypatch.setattr(intelion, "power_off", lambda: calls.append("off"))

    await gpu_power.watchdog_tick()

    assert calls == []


# ── _health_ok's real HTTP parsing (2026-07-03: gl_ready field) ──
# Everything above monkeypatches _health_ok itself; these exercise its real
# body, mocking aiohttp.ClientSession the same way test_map_import_file_url.py
# does, since _health_ok opens its own session rather than taking one in.

class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, *a, **kw):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_response(status, body):
    return patch(
        "utils.cloud.gpu_power.aiohttp.ClientSession",
        lambda *a, **kw: _FakeSession(_FakeResponse(status, body)),
    )


async def test_health_ok_true_when_gl_ready_true():
    with _patch_response(200, {"status": "ok", "gl_ready": True}):
        assert await gpu_power._health_ok() is True


async def test_health_ok_false_when_gl_not_ready():
    # 2026-07-03 incident: process is up (200) but GLX isn't — must NOT be
    # treated as ready just because the socket answered.
    with _patch_response(200, {"status": "ok", "gl_ready": False}):
        assert await gpu_power._health_ok() is False


async def test_health_ok_defaults_true_when_field_missing():
    # Backward compat: an old, not-yet-redeployed worker with no gl_ready key
    # at all shouldn't wedge the wake loop forever.
    with _patch_response(200, {"status": "ok"}):
        assert await gpu_power._health_ok() is True


async def test_health_ok_false_on_non_200_regardless_of_body():
    with _patch_response(500, {"status": "ok", "gl_ready": True}):
        assert await gpu_power._health_ok() is False
