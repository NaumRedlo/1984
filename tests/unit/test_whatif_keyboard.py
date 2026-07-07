"""The `map` card's accordion keyboard (collapsible mod/accuracy sections)
and its callback handler (bot/handlers/maplink/whatif.py). Direct calls with
SimpleNamespace CallbackQuery objects + a patched _build_whatif_data, no full
aiogram dispatch — mirrors test_scorelink_handler.py's style.

callback_data shape: wif:<beatmap_id>:<acc_x10>:<mods>:<view>:<action>"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot.handlers.maplink.whatif as w


@pytest.fixture(autouse=True)
def _patch_lang():
    async def fake(uid):
        return "EN"
    with patch.object(w, "get_language", fake):
        yield


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

    return SimpleNamespace(data=data, message=message or SimpleNamespace(),
                           from_user=SimpleNamespace(id=1), answer=answer), answers


def _msg_with_edit():
    """A message stub capturing both edit_media (card re-render) and
    edit_reply_markup (pure view toggle)."""
    calls = {"media": [], "markup": []}

    async def edit_media(**kwargs):
        calls["media"].append(kwargs)

    async def edit_reply_markup(**kwargs):
        calls["markup"].append(kwargs)

    return SimpleNamespace(edit_media=edit_media, edit_reply_markup=edit_reply_markup), calls


# ── _whatif_keyboard structure ───────────────────────────────────────────

def test_collapsed_by_default_shows_only_headers_and_bottom_row():
    kb = w._whatif_keyboard(129891, 94.0, "", "https://osu.ppy.sh/b/1")
    assert kb.inline_keyboard[0][0].text == "🎛 Mods ▸"
    assert kb.inline_keyboard[1][0].text == "🎯 Accuracy ▸"
    assert [b.text for b in kb.inline_keyboard[2]] == ["🏆 Leaderboard", "🔗 osu!"]
    assert len(kb.inline_keyboard) == 3


def test_ru_locale_translates_the_section_and_leaderboard_labels():
    kb = w._whatif_keyboard(129891, 94.0, "", "https://osu.ppy.sh/b/1", lang="ru")
    assert kb.inline_keyboard[0][0].text == "🎛 Моды ▸"
    assert kb.inline_keyboard[1][0].text == "🎯 Точность ▸"
    assert kb.inline_keyboard[2][0].text == "🏆 Топ карты"


def test_mods_section_expands_to_all_five_toggles():
    kb = w._whatif_keyboard(129891, 94.0, "HDDT", "https://osu.ppy.sh/b/1", view=w._VIEW_MODS)
    assert kb.inline_keyboard[0][0].text == "🎛 Mods ▾"
    mod_row = kb.inline_keyboard[1]
    labels = {btn.text.strip("• ") for btn in mod_row}
    assert labels == {"EZ", "HD", "HR", "DT", "NF"}
    assert {b.text for b in mod_row if b.text.startswith("•")} == {"• HD •", "• DT •"}


def test_accuracy_section_expands_to_steps_and_readout():
    kb = w._whatif_keyboard(129891, 94.0, "", "https://osu.ppy.sh/b/1", view=w._VIEW_ACC)
    assert kb.inline_keyboard[1][0].text == "🎯 Accuracy ▾"
    acc_row = kb.inline_keyboard[2]
    assert [b.text for b in acc_row] == ["-1", "-0.5", "-0.1", "94.0%", "+0.1", "+0.5", "+1"]


def test_both_sections_can_be_open_at_once():
    kb = w._whatif_keyboard(129891, 94.0, "", "https://osu.ppy.sh/b/1",
                            view=w._VIEW_MODS | w._VIEW_ACC)
    texts = [row[0].text for row in kb.inline_keyboard]
    assert texts[0] == "🎛 Mods ▾" and "🎯 Accuracy ▾" in texts


def test_header_buttons_toggle_their_own_view_bit():
    kb = w._whatif_keyboard(129891, 94.0, "HR", "https://osu.ppy.sh/b/1", view=0)
    assert kb.inline_keyboard[0][0].callback_data == "wif:129891:940:HR:0:vm"
    assert kb.inline_keyboard[1][0].callback_data == "wif:129891:940:HR:0:va"


def test_bottom_row_has_map_leaderboard_and_osu_link():
    kb = w._whatif_keyboard(129891, 94.0, "", "https://osu.ppy.sh/b/1", view=3)
    bottom = kb.inline_keyboard[-1]
    assert bottom[0].callback_data == "lbm:129891"   # local "Топ карты"
    assert bottom[1].url == "https://osu.ppy.sh/b/1"


def test_callback_data_roundtrips_view_and_state():
    kb = w._whatif_keyboard(129891, 94.5, "HR", "https://osu.ppy.sh/b/1", view=w._VIEW_ACC)
    # first accuracy step button "-1" lives in the expanded accuracy row
    sample = kb.inline_keyboard[2][0].callback_data
    assert sample == "wif:129891:945:HR:2:a-10"


# ── whatif_callback behaviour ─────────────────────────────────────────────

async def test_noop_action_is_a_pure_answer():
    cb, answers = _cb("wif:129891:940::0:noop")
    await w.whatif_callback(cb, osu_api_client=SimpleNamespace())
    assert len(answers) == 1


async def test_malformed_callback_data_is_ignored():
    cb, answers = _cb("wif:not-enough-parts")
    await w.whatif_callback(cb, osu_api_client=SimpleNamespace())
    assert len(answers) == 1


async def test_view_toggle_edits_markup_only_no_rerender():
    message, calls = _msg_with_edit()
    cb, answers = _cb("wif:129891:940:HR:0:vm", message=message)
    # No _build_whatif_data / render should be touched for a pure view toggle.
    with patch.object(w, "_build_whatif_data", side_effect=AssertionError("should not render")):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())
    assert len(calls["markup"]) == 1 and len(calls["media"]) == 0
    # toggled the mods bit on -> the returned keyboard shows it expanded
    kb = calls["markup"][0]["reply_markup"]
    assert kb.inline_keyboard[0][0].text == "🎛 Mods ▾"
    assert len(answers) == 1


async def test_accuracy_step_clamps_at_upper_bound():
    message, calls = _msg_with_edit()
    cb, answers = _cb("wif:129891:995:HR:2:a+10", message=message)

    async def fake_build(ref, accuracy, mods_str, api, lang="en"):
        assert accuracy == 100.0  # clamped from 99.5 + 1.0
        return _sample_data(accuracy=accuracy, mods=mods_str)

    with patch.object(w, "_build_whatif_data", fake_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())

    assert len(calls["media"]) == 1
    assert len(answers) == 1


async def test_accuracy_step_clamps_at_lower_bound():
    message, calls = _msg_with_edit()
    cb, answers = _cb("wif:129891:5:HR:2:a-10", message=message)

    async def fake_build(ref, accuracy, mods_str, api, lang="en"):
        assert accuracy == 0.1  # clamped from 0.5 - 1.0
        return _sample_data(accuracy=accuracy, mods=mods_str)

    with patch.object(w, "_build_whatif_data", fake_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())

    assert len(calls["media"]) == 1


async def test_mod_toggle_adds_then_removes():
    message, calls = _msg_with_edit()
    captured = {}

    async def capturing_build(ref, accuracy, mods_str, api, lang="en"):
        captured["mods"] = mods_str
        return _sample_data(accuracy=accuracy, mods=mods_str)

    cb_on, _ = _cb("wif:129891:940::1:mHD", message=message)
    with patch.object(w, "_build_whatif_data", capturing_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb_on, osu_api_client=SimpleNamespace())
    assert captured["mods"] == "HD"

    cb_off, _ = _cb("wif:129891:940:HD:1:mHD", message=message)
    with patch.object(w, "_build_whatif_data", capturing_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb_off, osu_api_client=SimpleNamespace())
    assert captured["mods"] == ""


async def test_mod_toggle_preserves_whatif_mod_set_order_and_view():
    message, calls = _msg_with_edit()
    captured = {}

    async def capturing_build(ref, accuracy, mods_str, api, lang="en"):
        captured["mods"] = mods_str
        return _sample_data(accuracy=accuracy, mods=mods_str)

    # Start with DT active + mods section open (view=1), toggle HD on ->
    # "HDDT" (WHATIF_MOD_SET order) and the keyboard stays expanded.
    cb, _ = _cb("wif:129891:940:DT:1:mHD", message=message)
    with patch.object(w, "_build_whatif_data", capturing_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async",
                      new=lambda data: _fake_png()):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())
    assert captured["mods"] == "HDDT"
    kb = calls["media"][0]["reply_markup"]
    assert kb.inline_keyboard[0][0].text == "🎛 Mods ▾"  # view preserved


async def test_build_data_failure_shows_alert_not_crash():
    message, calls = _msg_with_edit()
    cb, answers = _cb("wif:129891:940::1:mHD", message=message)

    async def failing_build(ref, accuracy, mods_str, api, lang="en"):
        return None

    with patch.object(w, "_build_whatif_data", failing_build):
        await w.whatif_callback(cb, osu_api_client=SimpleNamespace())

    assert len(calls["media"]) == 0
    assert answers and answers[0][1].get("show_alert") is True


def _fake_png():
    from io import BytesIO

    async def _inner():
        return BytesIO(b"fake-png-bytes")
    return _inner()
