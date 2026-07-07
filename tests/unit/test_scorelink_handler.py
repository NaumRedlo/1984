"""The score-link auto-detect handler (bot/handlers/scorelink/handlers.py).
Direct handler calls with SimpleNamespace messages + fake async deps, no
full aiogram dispatch — mirrors test_render_osr_confirm.py's style."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import bot.handlers.scorelink.handlers as sl


def _msg(text, chat_type="group"):
    sent = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=99)

    async def answer_photo(*a, **k):
        return sent

    return SimpleNamespace(
        text=text, from_user=SimpleNamespace(id=1, first_name="Tester", username="tester"),
        chat=SimpleNamespace(id=1, type=chat_type), answer_photo=answer_photo,
    ), sent


def _raw_score(**overrides):
    score = {
        "id": 555, "accuracy": 0.99, "passed": True, "rank": "S", "pp": 300.0,
        "max_combo": 720, "mods": [], "statistics": {"great": 550, "ok": 8, "meh": 0, "miss": 0},
        "beatmap": {"id": 1, "version": "v", "difficulty_rating": 5.0, "cs": 4, "ar": 9,
                    "accuracy": 8, "drain": 5, "bpm": 180, "total_length": 100, "max_combo": 720,
                    "status": "ranked"},
        "beatmapset": {"id": 2, "artist": "a", "title": "t", "creator": "c", "user_id": 3},
        "user": {"id": 3, "username": "scoreowner", "cover": {"url": "http://x/cover.jpg"}},
        "ended_at": "2026-01-01T00:00:00Z",
        "replay": False,
    }
    score.update(overrides)
    return score


async def test_no_ref_found_is_a_noop():
    message, _ = _msg("just chatting, no links here")
    api = SimpleNamespace()
    called = []

    async def fake_get_score(*a, **k):
        called.append(1)
    api.get_score = fake_get_score

    await sl.on_score_link(message, api)
    assert called == []


async def test_get_score_none_is_silent():
    message, _ = _msg("https://osu.ppy.sh/scores/555")

    async def fake_get_score(*a, **k):
        return None
    api = SimpleNamespace(get_score=fake_get_score)

    # Should not raise, and should not attempt to send anything.
    await sl.on_score_link(message, api)


async def test_happy_path_remembers_context_and_omits_render_button_without_replay():
    message, sent = _msg("https://osu.ppy.sh/scores/555")

    async def fake_get_score(score_id, mode=None):
        return _raw_score(replay=False)
    api = SimpleNamespace(get_score=fake_get_score)

    remembered = []

    def fake_remember(chat_id, message_id, data):
        remembered.append((chat_id, message_id, data))

    async def fake_generate(data):
        return BytesIO(b"fake-png-bytes")

    async def fake_get_language(_tg_id):
        return "EN"

    with patch.object(sl, "remember_message_context", fake_remember), \
         patch.object(sl, "get_language", fake_get_language), \
         patch.object(sl.card_renderer, "generate_recent_card_async", fake_generate):
        await sl.on_score_link(message, api)

    assert len(remembered) == 1
    chat_id, message_id, data = remembered[0]
    assert (chat_id, message_id) == (1, 99)
    assert data["card_mode"] == "shared"
    assert data["username"] == "scoreowner"


async def test_render_button_present_when_replay_available():
    message, sent = _msg("https://osu.ppy.sh/scores/555")

    async def fake_get_score(score_id, mode=None):
        return _raw_score(replay=True)
    api = SimpleNamespace(get_score=fake_get_score)

    captured_kb = []

    async def fake_answer_photo(*a, **k):
        captured_kb.append(k.get("reply_markup"))
        return sent
    message.answer_photo = fake_answer_photo

    async def fake_generate(data):
        return BytesIO(b"fake-png-bytes")

    async def fake_get_language(_tg_id):
        return "EN"

    with patch.object(sl, "remember_message_context", lambda *a, **k: None), \
         patch.object(sl, "get_language", fake_get_language), \
         patch.object(sl.card_renderer, "generate_recent_card_async", fake_generate):
        await sl.on_score_link(message, api)

    assert len(captured_kb) == 1
    kb = captured_kb[0]
    all_texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "🎬 Render" in all_texts


async def test_render_button_absent_when_no_replay():
    message, sent = _msg("https://osu.ppy.sh/scores/555")

    async def fake_get_score(score_id, mode=None):
        return _raw_score(replay=False)
    api = SimpleNamespace(get_score=fake_get_score)

    captured_kb = []

    async def fake_answer_photo(*a, **k):
        captured_kb.append(k.get("reply_markup"))
        return sent
    message.answer_photo = fake_answer_photo

    async def fake_generate(data):
        return BytesIO(b"fake-png-bytes")

    async def fake_get_language(_tg_id):
        return "EN"

    with patch.object(sl, "remember_message_context", lambda *a, **k: None), \
         patch.object(sl, "get_language", fake_get_language), \
         patch.object(sl.card_renderer, "generate_recent_card_async", fake_generate):
        await sl.on_score_link(message, api)

    kb = captured_kb[0]
    all_texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "🎬 Render" not in all_texts


async def test_slash_command_carrying_a_score_link_is_ignored():
    message, _ = _msg("/somecommand https://osu.ppy.sh/scores/555")
    called = []

    async def fake_get_score(*a, **k):
        called.append(1)
    api = SimpleNamespace(get_score=fake_get_score)

    await sl.on_score_link(message, api)
    assert called == []
