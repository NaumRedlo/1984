"""The registration language prompt's callback (bot/handlers/auth/handlers.
cb_registration_language): ownership check + set_language. Direct handler call
with a fake CallbackQuery, mirroring test_gpu_watchdog_admin.py's style."""

from types import SimpleNamespace

import bot.handlers.auth.handlers as auth
from utils import language as lang_mod


def _cb(data, from_id):
    edits = []

    async def edit_text(text, **kwargs):
        edits.append(text)

    async def answer(*a, **k):
        pass

    return SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=from_id),
        message=SimpleNamespace(edit_text=edit_text),
        answer=answer,
    ), edits


async def test_sets_language_for_matching_user(monkeypatch):
    calls = []

    async def fake_set(tg_id, lang):
        calls.append((tg_id, lang))

    monkeypatch.setattr(auth, "set_language", fake_set)
    cb, edits = _cb("reglang:111:RU", 111)

    await auth.cb_registration_language(cb)

    assert calls == [(111, "RU")]
    assert "Русский" in edits[0]


async def test_rejects_tap_from_a_different_user(monkeypatch):
    calls = []

    async def fake_set(tg_id, lang):
        calls.append((tg_id, lang))

    monkeypatch.setattr(auth, "set_language", fake_set)
    cb, edits = _cb("reglang:111:RU", 999)  # bystander in the group

    await auth.cb_registration_language(cb)

    assert calls == []
    assert edits == []


async def test_ignores_malformed_language_code(monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "set_language", lambda tg_id, lang: calls.append(1))
    cb, edits = _cb("reglang:111:XX", 111)

    await auth.cb_registration_language(cb)

    assert calls == []
