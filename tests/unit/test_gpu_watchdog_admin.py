"""Buttons on the GPU watchdog's idle-server prompt
(bot/handlers/admin/gpu_watchdog.py). Direct handler calls with a fake
CallbackQuery, mirroring test_settings_menu.py's style — no aiogram dispatch."""

from types import SimpleNamespace

from bot.handlers.admin import gpu_watchdog as gw
from utils.cloud import gpu_power


def _cb(data):
    edits = []

    async def edit_text(text, **kwargs):
        edits.append(text)

    async def answer(*a, **k):
        pass

    cb = SimpleNamespace(data=data, message=SimpleNamespace(edit_text=edit_text), answer=answer)
    return cb, edits


async def test_off_button_powers_off_and_confirms(monkeypatch):
    async def fake_health_ok(timeout=5.0):
        return True

    async def fake_force_off(context):
        return True

    monkeypatch.setattr(gpu_power, "_health_ok", fake_health_ok)
    monkeypatch.setattr(gpu_power, "force_power_off", fake_force_off)
    cb, edits = _cb("gpuwd:off")

    await gw.cb_gpuwd_off(cb)

    assert edits == ["✅ GPU-сервер выключен."]


async def test_off_button_reports_failure(monkeypatch):
    async def fake_health_ok(timeout=5.0):
        return True

    async def fake_force_off(context):
        return False

    monkeypatch.setattr(gpu_power, "_health_ok", fake_health_ok)
    monkeypatch.setattr(gpu_power, "force_power_off", fake_force_off)
    cb, edits = _cb("gpuwd:off")

    await gw.cb_gpuwd_off(cb)

    assert "не получилось" in edits[0].lower()


async def test_off_button_noop_when_already_off(monkeypatch):
    async def fake_health_ok(timeout=5.0):
        return False  # already off

    called = []

    async def fake_force_off(context):
        called.append(1)
        return True

    monkeypatch.setattr(gpu_power, "_health_ok", fake_health_ok)
    monkeypatch.setattr(gpu_power, "force_power_off", fake_force_off)
    cb, edits = _cb("gpuwd:off")

    await gw.cb_gpuwd_off(cb)

    assert called == []
    assert "уже выключен" in edits[0].lower()


async def test_snooze_button_sets_snooze_and_confirms(monkeypatch):
    monkeypatch.setattr(gpu_power, "_watchdog_snooze_until", None)
    cb, edits = _cb("gpuwd:snooze:30")

    await gw.cb_gpuwd_snooze(cb)

    assert gpu_power._watchdog_snooze_until is not None
    assert "30 мин" in edits[0]


async def test_snooze_button_bad_value_defaults_to_30(monkeypatch):
    cb, edits = _cb("gpuwd:snooze:notanumber")
    await gw.cb_gpuwd_snooze(cb)
    assert "30 мин" in edits[0]
