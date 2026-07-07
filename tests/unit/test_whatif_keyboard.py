"""The `map` card's interactive keyboard (mod toggles + accuracy steps) and
its callback handler (bot/handlers/maplink/whatif.py). Direct calls with
SimpleNamespace CallbackQuery objects + a patched _build_whatif_data, no
full aiogram dispatch — mirrors test_scorelink_handler.py's style."""

from types import SimpleNamespace
from unittest.mock import patch

import bot.handlers.maplink.whatif as w


def _sample_data(**overrides):
    data = {
        "beatmap_id": 129891, "beatmapset_id": 39804,
        "artist": "xi", "title": "FREEDOM DiVE", "version": "FOUR DIMENSIONS",
        "creator": "Nakagawa-Kanon", "status": "ranked", "cover_url": None,
        "url": "https://osu.ppy.sh/beatmapsets/39804#osu/129891",
        "star_rating": 7.42, "accuracy": 94.0, "mods": "", "pp": 227,
        "max_combo": 720, "count_300": 550, "count_100": 8, "count_50": 0, "count_miss": 0,
        "cs": 4.4, "ar": 10.3, "od": 9.7, "hp_drain": 8.0, "bpm": 180, "length": 126,
        "brackets": {95.0: 240.0, 98.0: 280.0, 99.0: 300.0, 100.0: 330.0},
    }
    data.update(overrides)
    return data


def _cb(data, message=None):
    answers = []

    async def answer(*a, **k):
        answers.append((a, k))

    return SimpleNamespace(data=data, message=message or SimpleNamespace(), answer=answer), answers


def _msg_with_edit():
    calls = []

    async def edit_media(**kwargs):
        calls.append(kwargs)

    return SimpleNamespace(edit_media=edit_media), calls


# ── _whatif_keyboard structure ───────────────────────────────────────────
# Layout: [🎛 Моды] / [mod toggles ×5] / [🎯 Точность] / [acc steps ×7] / [🔗 osu!]

def test_keyboard_has_mods_and_accuracy_section_headers():
    kb = w._whatif_keyboard(129891, 94.0, "", "https://osu.ppy.sh/b/1")
    assert kb.inline_keyboard[0][0].text == "🎛 Моды"
    assert kb.inline_keyboard[2][0].text == "🎯 Точность"


def test_keyboard_marks_active_mods_and_lists_all_five():
    kb = w._whatif_keyboard(129891, 94.0, "HDDT", "https://osu.ppy.sh/b/1")
    mod_row = kb.inline_keyboard[1]
    labels = {btn.text.strip("• ") for btn in mod_row}
    assert labels == {"EZ", "HD", "HR", "DT", "NF"}
    active_labels = [btn.text for btn in mod_row if btn.text.startswith("•")]
    assert set(active_labels) == {"• HD •", "• DT •"}


def test_keyboard_accuracy_row_has_six_steps_and_a_readout():
    kb = w._whatif_keyboard(129891, 94.0, "", "https://osu.ppy.sh/b/1")
    acc_row = kb.inline_keyboard[3]
    assert [b.text for b in acc_row] == ["-1", "-0.5", "-0.1", "94.0%", "+0.1", "+0.5", "+1"]


def test_keyboard_has_osu_link_button():
    kb = w._whatif_keyboard(129891, 94.0, "", "https://osu.ppy.sh/b/1")
    link_row = kb.inline_keyboard[4]
    assert link_row[0].url == "https://osu.ppy.sh/b/1"


def test_callback_data_roundtrips_beatmap_id_accuracy_and_mods():
    kb = w._whatif_keyboard(129891, 94.5, "HR", "https://osu.ppy.sh/b/1")
    sample = kb.inline_keyboard[3][0].callback_data  # "-1" button
    assert sample == "wif:129891:945:HR:a-10"


# ── whatif_callback behaviour ─────────────────────────────────────────────

async def test_noop_action_is_a_pure_answer():
    cb, answers = _cb("wif:129891:940::noop")
    await w.whatif_callback(cb, osu_api_client=SimpleNamespace())
    assert len(answers) == 1


async def test_malformed_callback_data_is_ignored():
    cb, answers = _cb("wif:not-enough-parts")
    await w.whatif_callback(cb, osu_api_client=SimpleNamespace())
    assert len(answers) == 1


async def test_accuracy_step_clamps_at_upper_bound():
    message, edits = _msg_with_edit()
    cb, answers = _cb("wif:129891:995:HR:a+10", message=message)

    async def fake_build(ref, accuracy, mods_str, api):
        assert accuracy == 100.0  # clamped from 99.5 + 1.0
        return _sample_data(accuracy=accuracy, mods=mods_str)

    with patch.object(w, "_build_whatif_data", fake_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())

    assert len(edits) == 1
    assert len(answers) == 1


async def test_accuracy_step_clamps_at_lower_bound():
    message, edits = _msg_with_edit()
    cb, answers = _cb("wif:129891:5:HR:a-10", message=message)

    async def fake_build(ref, accuracy, mods_str, api):
        assert accuracy == 0.1  # clamped from 0.5 - 1.0
        return _sample_data(accuracy=accuracy, mods=mods_str)

    with patch.object(w, "_build_whatif_data", fake_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())

    assert len(edits) == 1


async def test_mod_toggle_adds_then_removes():
    message, edits = _msg_with_edit()

    async def fake_build(ref, accuracy, mods_str, api):
        return _sample_data(accuracy=accuracy, mods=mods_str)

    # Toggling HD on from nomod.
    cb_on, _ = _cb("wif:129891:940::mHD", message=message)
    captured = {}

    async def capturing_build(ref, accuracy, mods_str, api):
        captured["mods"] = mods_str
        return _sample_data(accuracy=accuracy, mods=mods_str)

    with patch.object(w, "_build_whatif_data", capturing_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb_on, osu_api_client=SimpleNamespace())
    assert captured["mods"] == "HD"

    # Toggling HD off again from "HD".
    cb_off, _ = _cb("wif:129891:940:HD:mHD", message=message)
    with patch.object(w, "_build_whatif_data", capturing_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb_off, osu_api_client=SimpleNamespace())
    assert captured["mods"] == ""


async def test_mod_toggle_preserves_whatif_mod_set_order():
    message, edits = _msg_with_edit()
    captured = {}

    async def capturing_build(ref, accuracy, mods_str, api):
        captured["mods"] = mods_str
        return _sample_data(accuracy=accuracy, mods=mods_str)

    # Start with DT active, toggle on HD -> should come out "HDDT" (WHATIF_MOD_SET order),
    # not "DTHD" (click order).
    cb, _ = _cb("wif:129891:940:DT:mHD", message=message)
    with patch.object(w, "_build_whatif_data", capturing_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())
    assert captured["mods"] == "HDDT"


async def test_build_data_failure_shows_alert_not_crash():
    message, edits = _msg_with_edit()
    cb, answers = _cb("wif:129891:940::mHD", message=message)

    async def failing_build(ref, accuracy, mods_str, api):
        return None

    with patch.object(w, "_build_whatif_data", failing_build):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())

    assert len(edits) == 0
    assert answers and answers[0][1].get("show_alert") is True


def _fake_png():
    from io import BytesIO

    async def _inner():
        return BytesIO(b"fake-png-bytes")
    return _inner()
