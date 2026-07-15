"""On-demand GPU power coordinator (utils/cloud/gpu_power)."""

import asyncio

import pytest
from unittest.mock import patch

from utils.cloud import gpu_power
from utils.cloud import intelion


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(gpu_power, "_active", 0)
    monkeypatch.setattr(gpu_power, "_wake_task", None)
    monkeypatch.setattr(gpu_power, "_rebooting", False)
    monkeypatch.setattr(gpu_power, "_reboot_task", None)
    monkeypatch.setattr(gpu_power, "_bot", None)
    monkeypatch.setattr(gpu_power, "_watchdog_snooze_until", None)
    monkeypatch.setattr(gpu_power, "_HEALTH_POLL_SECONDS", 0)
    monkeypatch.setattr(gpu_power, "_DRAIN_POLL_SECONDS", 0)
    monkeypatch.setattr(gpu_power, "RENDER_POWEROFF_RETRY_SECONDS", 0)
    # Long by default so tests that don't care about the reboot cycle never
    # accidentally trigger it mid-test; specific tests dial this down to
    # actually exercise the cycle.
    monkeypatch.setattr(gpu_power, "RENDER_REBOOT_CYCLE_SECONDS", 3600)
    gpu_power._reboot_wake_ready.clear()
    yield
    # A test-started reboot-cycle task must not leak into the next test.
    if gpu_power._reboot_task is not None and not gpu_power._reboot_task.done():
        gpu_power._reboot_task.cancel()


async def _poll_until(predicate, *, timeout: float = 2.0, interval: float = 0.01) -> None:
    """Real-time poll for tests exercising the background reboot loop —
    asyncio scheduling across real sleeps isn't deterministic enough to
    assert on a single tick."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "condition never became true"
        await asyncio.sleep(interval)


async def test_session_noop_when_autopower_off(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", False)

    calls = []
    monkeypatch.setattr(intelion, "power_on", lambda: calls.append("on"))
    monkeypatch.setattr(intelion, "power_off", lambda: calls.append("off"))

    async with gpu_power.session():
        pass

    assert calls == []  # power API never touched


async def test_session_wakes_and_stays_up(monkeypatch):
    """2026-07-15 redesign: a render no longer powers the server back off —
    it stays up and the perpetual reboot cycle takes over from here."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    calls = []

    async def fake_check():
        return {"can_start": True}

    health = {"up": False}

    async def fake_health(timeout=5.0):
        return health["up"]

    async def flip_up():
        calls.append("on")
        health["up"] = True

    async def fake_power_off():
        calls.append("off")

    monkeypatch.setattr(intelion, "power_on", flip_up)
    monkeypatch.setattr(intelion, "power_off", fake_power_off)
    monkeypatch.setattr(intelion, "get_start_check", fake_check)
    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    async with gpu_power.session():
        assert health["up"] is True  # ready inside the session

    assert calls == ["on"]  # never powered off after just one render
    assert gpu_power._active == 0
    assert gpu_power._reboot_task is not None and not gpu_power._reboot_task.done()


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

    # asyncio.sleep is patched process-wide, so the perpetual reboot-cycle
    # loop's own (unrelated) RENDER_REBOOT_CYCLE_SECONDS sleep shows up here
    # too — only assert on the specific _HEALTH_POLL_SECONDS value this test
    # actually cares about never firing.
    assert gpu_power._HEALTH_POLL_SECONDS not in sleep_calls


async def test_session_skips_wake_when_already_up(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    calls = []
    monkeypatch.setattr(intelion, "power_on", lambda: calls.append("on"))
    monkeypatch.setattr(intelion, "power_off", lambda: calls.append("off"))

    async def fake_health(timeout=5.0):
        return True  # already up

    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    async with gpu_power.session():
        pass

    assert calls == []  # no power_on needed, and nothing powers it off afterward


async def test_second_render_reuses_the_warm_server(monkeypatch):
    """Instead of a short idle-then-off warm window, the server just stays up
    indefinitely — a second render doesn't pay any wake cost at all."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

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
    async with gpu_power.session():
        pass

    assert calls == ["on"]  # only ever woke once


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


async def test_force_power_off_cancels_the_reboot_cycle(monkeypatch):
    """An explicit admin "turn it off" must actually stay off — not get
    powered back on by the perpetual cycle 45 minutes later."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    fake_task = asyncio.get_event_loop().create_future()
    monkeypatch.setattr(gpu_power, "_reboot_task", fake_task)
    monkeypatch.setattr(gpu_power, "_rebooting", True)
    monkeypatch.setattr(intelion, "power_off", lambda: None)

    await gpu_power.force_power_off("test")

    assert fake_task.cancelled()
    assert gpu_power._reboot_task is None
    assert gpu_power._rebooting is False


async def test_watchdog_skips_when_active_or_rebooting(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    calls = []
    monkeypatch.setattr(intelion, "power_off", lambda: calls.append("off"))

    # Active render in flight — must not touch power.
    monkeypatch.setattr(gpu_power, "_active", 1)
    await gpu_power.watchdog_tick()
    assert calls == []

    # The scheduled reboot is in progress — must not double-fire either.
    monkeypatch.setattr(gpu_power, "_active", 0)
    monkeypatch.setattr(gpu_power, "_rebooting", True)
    await gpu_power.watchdog_tick()
    assert calls == []


async def test_watchdog_leaves_alone_while_reboot_cycle_is_tracked(monkeypatch):
    """A live (but currently idle-between-renders) reboot-cycle task is the
    NORMAL steady state now, not an anomaly — the watchdog must not treat it
    as an orphaned server and prompt an admin every tick."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)
    monkeypatch.setattr(gpu_power, "_active", 0)
    monkeypatch.setattr(gpu_power, "_rebooting", False)
    fake_task = asyncio.get_event_loop().create_future()
    monkeypatch.setattr(gpu_power, "_reboot_task", fake_task)

    prompts = []
    monkeypatch.setattr(gpu_power, "_prompt_admins_idle_server", lambda: prompts.append(1))

    await gpu_power.watchdog_tick()

    assert prompts == []
    fake_task.cancel()


async def test_watchdog_noop_when_autopower_off(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", False)

    calls = []
    monkeypatch.setattr(intelion, "power_off", lambda: calls.append("off"))

    await gpu_power.watchdog_tick()

    assert calls == []


# ── resume_if_already_up: silent re-adoption on bot restart ─────────────────
# 2026-07-15: a bot restart while the server was legitimately mid-cycle used
# to be indistinguishable from a genuinely orphaned server — the watchdog
# would prompt an admin every time. Now bot startup itself re-adopts an
# already-up server into a fresh cycle before the watchdog ever gets a look.

async def test_resume_silently_readopts_an_already_up_server(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    async def fake_health(timeout=5.0):
        return True

    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    prompts = []
    monkeypatch.setattr(gpu_power, "_prompt_admins_idle_server", lambda: prompts.append(1))

    await gpu_power.resume_if_already_up()

    assert gpu_power._reboot_task is not None and not gpu_power._reboot_task.done()
    assert prompts == []  # no admin prompt — this is routine, not an anomaly


async def test_resume_does_nothing_when_server_is_off(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

    async def fake_health(timeout=5.0):
        return False

    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    await gpu_power.resume_if_already_up()

    assert gpu_power._reboot_task is None


async def test_resume_noop_when_autopower_off(monkeypatch):
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", False)

    calls = []
    monkeypatch.setattr(gpu_power, "_health_ok", lambda timeout=5.0: calls.append(1))

    await gpu_power.resume_if_already_up()

    assert calls == []  # didn't even check — autopower is off
    assert gpu_power._reboot_task is None


def test_watchdog_prompt_keyboard_drops_the_two_hour_option():
    """The admin never picks "leave it on 2 hours" — only "off now" or a
    30-min snooze when there's an actual reason. Dropped as dead UI weight."""
    kb = gpu_power._watchdog_prompt_kb()
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == ["gpuwd:off", "gpuwd:snooze:30"]
    assert not any("120" in c for c in cbs)


# ── The perpetual reboot cycle itself ──────────────────────────────────────

async def test_reboot_cycle_fires_and_resumes_forever(monkeypatch):
    """After RENDER_REBOOT_CYCLE_SECONDS, the server is stopped and
    immediately restarted (not just left off) — and the loop keeps going,
    ready to do it again."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)
    monkeypatch.setattr(gpu_power, "RENDER_REBOOT_CYCLE_SECONDS", 0.02)

    calls = []
    health = {"up": False}

    async def fake_power_on():
        calls.append("on")
        health["up"] = True

    async def fake_power_off():
        calls.append("off")
        health["up"] = False

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
    assert calls == ["on"]

    await _poll_until(lambda: calls == ["on", "off", "on"] and not gpu_power._rebooting)
    assert gpu_power._reboot_task is not None and not gpu_power._reboot_task.done()
    assert gpu_power._rebooting is False  # settled back down after the restart


async def test_render_arriving_during_reboot_waits_and_gets_the_restart_message(monkeypatch):
    """A render that shows up while the scheduled reboot is in progress must
    NOT be told to try again — it should see a "please wait" message and then
    complete automatically once the fresh instance is ready. Drives
    `_rebooting`/`_wake_task`/`_reboot_wake_ready` directly rather than
    racing a real RENDER_REBOOT_CYCLE_SECONDS timer, so the scenario (and
    the exact moment the fresh task resolves) is deterministic."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)
    monkeypatch.setattr(gpu_power, "_rebooting", True)
    gpu_power._reboot_wake_ready.clear()

    resolved = {"done": False}

    async def fake_restart_wake():
        await asyncio.sleep(0.02)
        resolved["done"] = True

    fresh_task = asyncio.ensure_future(fake_restart_wake())
    monkeypatch.setattr(gpu_power, "_wake_task", fresh_task)

    async def flip_ready_soon():
        await asyncio.sleep(0.005)
        gpu_power._reboot_wake_ready.set()

    asyncio.ensure_future(flip_ready_soon())

    messages = []

    async def on_wake(text):
        messages.append(text)

    async with gpu_power.session(on_wake=on_wake):
        pass  # lands mid-reboot

    assert messages and "restart" in messages[0].lower()
    assert resolved["done"] is True   # actually waited for the fresh task
    assert gpu_power._active == 0


async def test_reboot_waits_for_in_flight_render_before_stopping(monkeypatch):
    """The reboot boundary must never interrupt a render already in
    progress — it waits for it to finish first."""
    monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)
    monkeypatch.setattr(gpu_power, "RENDER_REBOOT_CYCLE_SECONDS", 0.02)

    calls = []
    health = {"up": False}

    async def fake_power_on():
        calls.append("on")
        health["up"] = True

    async def fake_power_off():
        calls.append("off")
        health["up"] = False

    async def fake_check():
        return {"can_start": True}

    async def fake_health(timeout=5.0):
        return health["up"]

    monkeypatch.setattr(intelion, "power_on", fake_power_on)
    monkeypatch.setattr(intelion, "power_off", fake_power_off)
    monkeypatch.setattr(intelion, "get_start_check", fake_check)
    monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

    async with gpu_power.session():
        # Hold a render "in flight" well past the reboot boundary.
        await asyncio.sleep(0.05)
        assert gpu_power._rebooting is True   # cycle wants to reboot now...
        assert "off" not in calls             # ...but hasn't stopped the server yet

    await _poll_until(lambda: calls == ["on", "off", "on"])


def test_reboot_task_not_duplicated_across_renders(monkeypatch):
    """A second, concurrent 'idle' transition must not spawn a second
    perpetual loop."""
    async def run():
        monkeypatch.setattr(gpu_power, "RENDER_AUTOPOWER", True)

        async def fake_health(timeout=5.0):
            return True

        monkeypatch.setattr(gpu_power, "_health_ok", fake_health)

        async with gpu_power.session():
            pass
        first = gpu_power._reboot_task
        async with gpu_power.session():
            pass
        second = gpu_power._reboot_task
        assert first is second

    asyncio.run(run())


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
