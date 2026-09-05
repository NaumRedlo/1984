from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.handlers.profile import top_plays as tp

@pytest.fixture(autouse=True)
def _patch_lang(monkeypatch):
    async def fake(uid):
        return "EN"
    monkeypatch.setattr(tp, "get_language", fake)

async def test_build_payload_public_lookup_uses_viewer_language(monkeypatch):
    async def fake_get_language(tg_id):
        return "RU" if tg_id == 111 else "EN"

    monkeypatch.setattr(tp, "get_language", fake_get_language)

    async def fake_get_best_scores(osu_id, limit=100):
        return []

    fake_api = SimpleNamespace(get_user_best_scores=fake_get_best_scores)

    user_data = {"id": 999, "username": "someoneelse"}

    payload = await tp._build_payload(
        None, user_data, fake_api, None, public_lookup=True, viewer_tg_id=111,
    )
    assert payload["lang"] == "ru"

async def test_build_payload_registered_uses_viewer_not_subject_language(monkeypatch):
    async def fake_get_language(tg_id):

        return "RU" if tg_id == 111 else "EN"

    monkeypatch.setattr(tp, "get_language", fake_get_language)

    async def fake_fetch_best_scores(session, user_id):
        return []

    monkeypatch.setattr(tp, "_fetch_best_scores", fake_fetch_best_scores)

    subject = SimpleNamespace(
        id=1, telegram_id=222, osu_username="subject", country="US",
        avatar_url=None, cover_url=None, global_rank=100, player_pp=5000.0,
        accuracy=98.0,
    )
    payload = await tp._build_payload(None, subject, None, None, viewer_tg_id=111)
    assert payload["lang"] == "ru"

def _cb(data, from_id):
    deleted = []
    answers = []

    async def edit_text(*a, **k):
        pass

    async def delete():
        deleted.append(True)

    async def answer(*a, **k):
        answers.append((a, k))

    cb = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=from_id, username="viewer"),
        message=SimpleNamespace(edit_text=edit_text, delete=delete),
        answer=answer,
    )
    return cb, deleted, answers

@asynccontextmanager
async def _fake_session():
    yield SimpleNamespace()

async def test_tpp_open_allows_the_actual_viewer_on_a_cross_profile_lookup(monkeypatch):
    monkeypatch.setattr(tp, "get_db_session", _fake_session)

    fetched = []

    async def fake_get_registered_user(session, tg_id, tenant_chat_id):
        fetched.append(tg_id)
        return SimpleNamespace(
            id=1, telegram_id=tg_id, osu_username="subject", country="US",
            avatar_url=None, cover_url=None, global_rank=1, player_pp=1.0, accuracy=99.0,
        )

    monkeypatch.setattr(tp, "get_registered_user", fake_get_registered_user)

    handles_seen = []

    async def fake_build_payload(session, user, osu_api_client, tg_handle, **kwargs):
        handles_seen.append(tg_handle)
        return {"built": [], "username": "x", "handle": None, "country": "US",
                "avatar_url": None, "cover_url": None, "global_rank": 1,
                "player_pp": 1.0, "accuracy": 99.0, "lang": "en"}

    monkeypatch.setattr(tp, "_build_payload", fake_build_payload)

    rendered = []

    async def fake_render(message, uid, page, payload, *, edit):
        rendered.append(uid)

    monkeypatch.setattr(tp, "_render", fake_render)

    cb, deleted, answers = _cb("tpp|open|111|222", 111)
    await tp.on_tpp_open(cb)

    assert fetched == [222]
    assert rendered == [111]
    assert not any(a[1].get("show_alert") for a in answers)

    assert handles_seen == [None]

async def test_tpp_open_shows_own_handle_when_viewer_is_the_subject(monkeypatch):
    monkeypatch.setattr(tp, "get_db_session", _fake_session)

    async def fake_get_registered_user(session, tg_id, tenant_chat_id):
        return SimpleNamespace(
            id=1, telegram_id=tg_id, osu_username="subject", country="US",
            avatar_url=None, cover_url=None, global_rank=1, player_pp=1.0, accuracy=99.0,
        )

    monkeypatch.setattr(tp, "get_registered_user", fake_get_registered_user)

    handles_seen = []

    async def fake_build_payload(session, user, osu_api_client, tg_handle, **kwargs):
        handles_seen.append(tg_handle)
        return {"built": [], "username": "x", "handle": None, "country": "US",
                "avatar_url": None, "cover_url": None, "global_rank": 1,
                "player_pp": 1.0, "accuracy": 99.0, "lang": "en"}

    monkeypatch.setattr(tp, "_build_payload", fake_build_payload)

    async def fake_render(message, uid, page, payload, *, edit):
        pass

    monkeypatch.setattr(tp, "_render", fake_render)

    cb, deleted, answers = _cb("tpp|open|111|111", 111)
    await tp.on_tpp_open(cb)

    assert handles_seen == ["@viewer"]

async def test_tpp_open_rejects_a_bystander_who_is_neither_viewer(monkeypatch):
    monkeypatch.setattr(tp, "get_db_session", _fake_session)
    called = []

    async def fake_get_registered_user(*a, **k):
        called.append(1)

    monkeypatch.setattr(tp, "get_registered_user", fake_get_registered_user)

    cb, deleted, answers = _cb("tpp|open|111|222", 999)
    await tp.on_tpp_open(cb)

    assert called == []
    assert answers and answers[0][1].get("show_alert") is True

async def test_tpp_open_rejects_the_subject_clicking_someone_elses_copy(monkeypatch):
    monkeypatch.setattr(tp, "get_db_session", _fake_session)
    called = []

    async def fake_get_registered_user(*a, **k):
        called.append(1)

    monkeypatch.setattr(tp, "get_registered_user", fake_get_registered_user)

    cb, deleted, answers = _cb("tpp|open|111|222", 222)
    await tp.on_tpp_open(cb)

    assert called == []
    assert answers and answers[0][1].get("show_alert") is True

async def test_tpp_back_fetches_subject_but_authorizes_viewer(monkeypatch):
    monkeypatch.setattr(tp, "get_db_session", _fake_session)

    fetched = []

    async def fake_get_registered_user(session, tg_id, tenant_chat_id):
        fetched.append(tg_id)
        return SimpleNamespace(telegram_id=tg_id)

    monkeypatch.setattr(tp, "get_registered_user", fake_get_registered_user)

    import bot.handlers.profile.handlers as pf_handlers

    handles_seen = []

    async def fake_build_page_data(user, osu_api_client, session, tg_handle=None, viewer_tg_id=None):
        assert viewer_tg_id == 111
        handles_seen.append(tg_handle)
        return {"osu_id": 42, "lang": "ru"}

    kb_calls = []

    def fake_pf_keyboard(osu_id, subject_tg_id=None, viewer_tg_id=None, lang="en"):
        kb_calls.append((subject_tg_id, viewer_tg_id))
        return None

    with patch.object(pf_handlers, "_build_page_data", fake_build_page_data), \
         patch.object(pf_handlers, "_pf_keyboard", fake_pf_keyboard):
        card_calls = []

        async def fake_generate(data):
            from io import BytesIO
            card_calls.append(data)
            return BytesIO(b"fake")

        monkeypatch.setattr(tp.card_renderer, "generate_profile_dashboard_async", fake_generate)

        cb, deleted, answers = _cb("tpp|back|111|222", 111)
        await tp.on_tpp_back(cb)

    assert fetched == [222]
    assert kb_calls == [(222, 111)]

    assert handles_seen == [None]
