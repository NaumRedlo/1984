"""Passive beatmap-link auto-detect (bot/handlers/maplink/handlers.py).
Pasting a link now posts the interactive what-if card directly (100% nomod
default) instead of a plain static info card — direct handler calls with
SimpleNamespace messages + a patched _build_whatif_data/render, mirroring
test_scorelink_handler.py's style."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import bot.handlers.maplink.handlers as h


def _msg(text):
    sent = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=42)

    async def answer_photo(*a, **k):
        return sent

    return SimpleNamespace(text=text, answer_photo=answer_photo), sent


def _sample_data(**overrides):
    data = {
        "beatmap_id": 129891, "beatmapset_id": 39804,
        "artist": "xi", "title": "FREEDOM DiVE", "version": "FOUR DIMENSIONS",
        "creator": "Nakagawa-Kanon", "status": "ranked", "cover_url": None,
        "url": "https://osu.ppy.sh/beatmapsets/39804#osu/129891",
        "star_rating": 7.42, "accuracy": 100.0, "mods": "", "pp": 300,
        "max_combo": 720, "count_300": 558, "count_100": 0, "count_50": 0, "count_miss": 0,
        "cs": 4.4, "ar": 10.3, "od": 9.7, "hp_drain": 8.0, "bpm": 180, "length": 126,
        "brackets": {95.0: 240.0, 98.0: 280.0, 99.0: 300.0, 100.0: 330.0},
    }
    data.update(overrides)
    return data


async def test_no_link_is_a_noop():
    message, _ = _msg("just chatting, no links here")
    called = []

    async def fake_build(ref, accuracy, mods_str, api):
        called.append(1)
    with patch.object(h, "_build_whatif_data", fake_build):
        await h.on_beatmap_link(message, SimpleNamespace())
    assert called == []


async def test_command_carrying_a_link_is_ignored():
    message, _ = _msg("/somecommand https://osu.ppy.sh/beatmaps/129891")
    called = []

    async def fake_build(ref, accuracy, mods_str, api):
        called.append(1)
    with patch.object(h, "_build_whatif_data", fake_build):
        await h.on_beatmap_link(message, SimpleNamespace())
    assert called == []


async def test_link_posts_interactive_card_at_default_accuracy():
    message, sent = _msg("check this map https://osu.ppy.sh/beatmaps/129891")

    captured = {}

    async def fake_build(ref, accuracy, mods_str, api):
        captured["ref"] = ref
        captured["accuracy"] = accuracy
        captured["mods_str"] = mods_str
        return _sample_data()

    remembered = []

    def fake_remember(chat_id, message_id, data):
        remembered.append((chat_id, message_id, data))

    async def fake_generate(data):
        return BytesIO(b"fake-png-bytes")

    with patch.object(h, "_build_whatif_data", fake_build), \
         patch.object(h, "remember_message_context", fake_remember), \
         patch.object(h.card_renderer, "generate_whatif_card_async", fake_generate):
        await h.on_beatmap_link(message, SimpleNamespace())

    assert captured["ref"].beatmap_id == 129891
    assert captured["accuracy"] == h._DEFAULT_ACCURACY == 100.0
    assert captured["mods_str"] == ""
    assert len(remembered) == 1
    chat_id, message_id, ctx = remembered[0]
    assert (chat_id, message_id) == (1, 42)
    assert ctx == {"beatmap_id": 129891, "beatmapset_id": 39804}


async def test_resolve_or_pp_failure_is_silent():
    message, _ = _msg("https://osu.ppy.sh/beatmaps/129891")

    async def failing_build(ref, accuracy, mods_str, api):
        return None

    with patch.object(h, "_build_whatif_data", failing_build):
        await h.on_beatmap_link(message, SimpleNamespace())  # must not raise
