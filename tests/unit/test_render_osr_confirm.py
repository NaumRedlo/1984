"""The confirm-before-render prompt for a bare .osr upload
(bot/handlers/profile/render.py). 2026-07-03: replaced the caption-trigger
("render"/"рендер") — any .osr now prompts a one-tap confirm instead, so an
accidental/spam upload doesn't render unprompted. Direct handler calls with
a fake CallbackQuery."""

from types import SimpleNamespace

import pytest
from osrparse.utils import GameMode

from bot.handlers.profile import render as r


@pytest.fixture(autouse=True)
def _patch_lang(monkeypatch):
    async def fake(uid):
        return "EN"
    monkeypatch.setattr(r.osr_handlers, "get_language", fake)


def _cb(data, from_id, reply_doc=None):
    deleted = []
    answers = []

    async def delete():
        deleted.append(True)

    async def answer(*a, **k):
        answers.append((a, k))

    reply_to_message = None
    if reply_doc is not None:
        reply_to_message = SimpleNamespace(
            document=reply_doc, from_user=SimpleNamespace(id=from_id),
        )

    cb = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=from_id),
        message=SimpleNamespace(delete=delete, reply_to_message=reply_to_message),
        answer=answer,
    )
    return cb, deleted, answers


def _doc(name="replay.osr"):
    return SimpleNamespace(file_name=name)


def test_confirm_kb_encodes_owner_in_both_buttons():
    kb = r._confirm_render_kb(12345)
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == ["rdrf:go:12345", "rdrf:no:12345"]


async def test_cancel_deletes_prompt_without_rendering(monkeypatch):
    called = []

    async def fake_render(*a, **k):
        called.append((a, k))

    monkeypatch.setattr(r.osr_handlers, "_render_uploaded_osr", fake_render)
    cb, deleted, answers = _cb("rdrf:no:111", 111, reply_doc=_doc())

    await r.cb_confirm_render_file(cb)

    assert deleted == [True]
    assert called == []


async def test_confirm_triggers_render_with_source_message_and_doc(monkeypatch):
    called = []

    async def fake_render(message, doc, osu_api_client=None, tenant_chat_id=None, lang="en"):
        called.append((message, doc, tenant_chat_id))

    monkeypatch.setattr(r.osr_handlers, "_render_uploaded_osr", fake_render)
    doc = _doc()
    cb, deleted, answers = _cb("rdrf:go:111", 111, reply_doc=doc)

    await r.cb_confirm_render_file(cb, tenant_chat_id=42)

    assert deleted == [True]
    assert len(called) == 1
    message, called_doc, tenant_chat_id = called[0]
    assert called_doc is doc
    assert tenant_chat_id == 42
    assert message is cb.message.reply_to_message


async def test_rejects_tap_from_a_different_user(monkeypatch):
    called = []

    async def fake_render(*a, **k):
        called.append(1)

    monkeypatch.setattr(r.osr_handlers, "_render_uploaded_osr", fake_render)
    cb, deleted, answers = _cb("rdrf:go:111", 999, reply_doc=_doc())  # bystander taps

    await r.cb_confirm_render_file(cb)

    assert called == []
    assert deleted == []
    assert answers and answers[0][1].get("show_alert") is True


async def test_missing_reply_to_message_shows_alert_instead_of_crashing(monkeypatch):
    called = []

    async def fake_render(*a, **k):
        called.append(1)

    monkeypatch.setattr(r.osr_handlers, "_render_uploaded_osr", fake_render)
    cb, deleted, answers = _cb("rdrf:go:111", 111, reply_doc=None)  # original message gone

    await r.cb_confirm_render_file(cb)

    assert called == []
    assert answers and answers[0][1].get("show_alert") is True


async def test_prompt_shown_for_any_osr_no_caption_needed(monkeypatch):
    replies = []

    async def fake_reply(text, **kwargs):
        replies.append((text, kwargs))

    monkeypatch.setattr(r.osr_handlers, "_check_cooldown", lambda tg_id: None)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=111), document=_doc(), reply=fake_reply,
    )

    await r.prompt_render_file(message)

    assert len(replies) == 1
    text, kwargs = replies[0]
    assert "render" in text.lower()
    assert kwargs["reply_markup"] is not None


# ── mode validation (2026-07-03) ──
# danser only renders osu!standard. A taiko/catch/mania .osr downloaded from
# the website used to sail all the way through to danser itself and fail
# there as an opaque "danser exited with code 1" — reported by the user after
# trying replays in the other 3 modes. Now rejected right after parsing.

class _WaitMsg:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)


async def _non_std_message(monkeypatch, mode):
    monkeypatch.setattr(r.osr_handlers, "_check_cooldown", lambda tg_id: None)
    monkeypatch.setattr(r.osr_handlers, "RENDER_WORKER_URL", "http://worker.example")  # skip local danser check

    wait_msg = _WaitMsg()

    async def fake_answer(text, **kwargs):
        return wait_msg

    async def fake_download(doc, destination):
        with open(destination, "wb") as f:
            f.write(b"fake .osr bytes")

    fake_replay = SimpleNamespace(mode=mode, beatmap_hash="abc", username="Player")
    monkeypatch.setattr(r.Replay, "from_string", staticmethod(lambda data: fake_replay))

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=111),
        answer=fake_answer,
        bot=SimpleNamespace(download=fake_download),
    )
    doc = _doc()

    await r._render_uploaded_osr(message, doc)
    return wait_msg


async def test_rejects_taiko_replay(monkeypatch):
    wait_msg = await _non_std_message(monkeypatch, GameMode.TAIKO)
    assert any("standard" in text.lower() for text in wait_msg.edits)


async def test_rejects_catch_replay(monkeypatch):
    wait_msg = await _non_std_message(monkeypatch, GameMode.CTB)
    assert any("standard" in text.lower() for text in wait_msg.edits)


async def test_rejects_mania_replay(monkeypatch):
    wait_msg = await _non_std_message(monkeypatch, GameMode.MANIA)
    assert any("standard" in text.lower() for text in wait_msg.edits)
