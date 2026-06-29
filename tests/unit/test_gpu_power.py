"""On-demand GPU power coordinator (utils/cloud/gpu_power)."""

import pytest

from utils.cloud import gpu_power
from utils.cloud import intelion


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(gpu_power, "_active", 0)
    monkeypatch.setattr(gpu_power, "_wake_task", None)
    monkeypatch.setattr(gpu_power, "_off_task", None)
    monkeypatch.setattr(gpu_power, "_HEALTH_POLL_SECONDS", 0)
    # Immediate power-off by default in tests; the warm-window test overrides this.
    monkeypatch.setattr(gpu_power, "RENDER_WARM_SECONDS", 0)


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
