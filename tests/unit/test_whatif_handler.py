"""The `map` command's argument parsing (bot/handlers/maplink/whatif.py).
Direct calls with SimpleNamespace messages, mirroring
test_render_osr_confirm.py's style.

The beatmap is ONLY ever resolved via context the bot already recorded for
the replied-to message (remember_message_context/get_message_context) —
there is no self-contained "map <link> <accuracy>" form and no parsing of
a raw link out of the reply's own text/caption."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.handlers.maplink import whatif as w
from utils.osu.helpers import remember_message_context


@pytest.fixture(autouse=True)
def _patch_lang():
    async def fake(uid):
        return "EN"
    with patch.object(w, "get_language", fake):
        yield


def _msg(reply=None, thread_id=None):
    return SimpleNamespace(reply_to_message=reply, message_thread_id=thread_id,
                           from_user=SimpleNamespace(id=1))


def _reply(chat_id=1, message_id=5):
    return SimpleNamespace(
        text=None, caption=None, forum_topic_created=None,
        message_id=message_id, chat=SimpleNamespace(id=chat_id),
    )


def _parse_with_context(args_text: str, ctx: dict | None):
    reply = _reply()
    with patch.object(w, "get_message_context", return_value=ctx):
        return w._parse_whatif_args(args_text, _msg(reply=reply))


def test_reply_to_bot_card_resolves_via_message_context():
    parsed, err = _parse_with_context("94 hr", {"beatmap_id": 129891, "beatmapset_id": 39804})
    assert err is None
    assert parsed.beatmap_ref.beatmap_id == 129891
    assert parsed.beatmap_ref.beatmapset_id == 39804
    assert parsed.accuracy == 94.0
    assert parsed.mods_str == "HR"


def test_no_reply_shows_usage():
    parsed, err = w._parse_whatif_args("94 hr", _msg())
    assert parsed is None
    assert "Reply" in err


def test_reply_with_no_recorded_context_shows_usage():
    parsed, err = _parse_with_context("94 hr", None)
    assert parsed is None
    assert "Reply" in err


def test_link_in_args_is_no_longer_parsed_as_the_beatmap():
    """A link typed directly in the command's own args must NOT be treated
    as the beatmap — only reply-context does that now."""
    parsed, err = _parse_with_context("https://osu.ppy.sh/beatmaps/129891 94 hr", None)
    assert parsed is None
    assert "Reply" in err


def test_missing_accuracy_is_an_error():
    parsed, err = _parse_with_context("", {"beatmap_id": 129891})
    assert parsed is None
    assert "accuracy" in err.lower()


def test_invalid_accuracy_text_is_an_error():
    parsed, err = _parse_with_context("abc", {"beatmap_id": 129891})
    assert parsed is None
    assert "Invalid accuracy" in err


def test_accuracy_out_of_range_is_an_error():
    parsed, err = _parse_with_context("150", {"beatmap_id": 129891})
    assert parsed is None
    assert "0" in err and "100" in err


def test_unknown_mod_is_an_error():
    parsed, err = _parse_with_context("94 xy", {"beatmap_id": 129891})
    assert parsed is None
    assert "mod" in err.lower()


def test_comma_decimal_accuracy_parses():
    parsed, err = _parse_with_context("94,5", {"beatmap_id": 129891})
    assert err is None
    assert parsed.accuracy == 94.5


def test_percent_sign_in_accuracy_parses():
    parsed, err = _parse_with_context("94%", {"beatmap_id": 129891})
    assert err is None
    assert parsed.accuracy == 94.0


def test_nomod_is_valid():
    parsed, err = _parse_with_context("98", {"beatmap_id": 129891})
    assert err is None
    assert parsed.mods_str == ""


# ── WhatifReplyFilter: must not misfire on ordinary replies ─────────────────
# 2026-07-08 bug report: replying "10 июля день" to an UNRELATED message
# ("10 July [is a] day") triggered the bot with "Unknown mod". Root cause:
# get_message_context's "latest beatmap_id in this chat" fallback (meant for
# the explicit `lbm` command) was also used by this bare-text reply trigger —
# any reply in a chat that had EVER shown a beatmap card resolved to that
# stale context regardless of what message was actually replied to, since the
# fallback ignores message_id entirely once the exact lookup misses.

def _real_reply(chat_id: int, message_id: int):
    return SimpleNamespace(
        text=None, caption=None, forum_topic_created=None,
        message_id=message_id, chat=SimpleNamespace(id=chat_id),
    )


async def test_bare_reply_to_unrelated_message_does_not_misfire(monkeypatch):
    chat_id = 424242
    # A card was posted earlier in this chat (message_id=1) ...
    remember_message_context(chat_id, 1, {"beatmap_id": 129891, "beatmapset_id": 39804})
    # ... but this reply targets a completely different, unrelated message.
    unrelated = _real_reply(chat_id, message_id=2)
    message = SimpleNamespace(
        text="10 июля день", reply_to_message=unrelated, message_thread_id=None,
        from_user=SimpleNamespace(id=1),
    )
    assert await w.WhatifReplyFilter()(message) is False


async def test_bare_reply_to_the_actual_card_still_fires():
    chat_id = 424243
    remember_message_context(chat_id, 1, {"beatmap_id": 129891, "beatmapset_id": 39804})
    card_reply = _real_reply(chat_id, message_id=1)
    message = SimpleNamespace(
        text="80 ez", reply_to_message=card_reply, message_thread_id=None,
        from_user=SimpleNamespace(id=1),
    )
    result = await w.WhatifReplyFilter()(message)
    assert result == {"whatif_text": "80 ez"}


# ── cmd_whatif: reply-form edits the card in place ────────────────────────

def _whatif_data(**overrides):
    data = {
        "beatmap_id": 129891, "beatmapset_id": 39804, "mods": "EZ", "accuracy": 80.0,
        "url": "https://osu.ppy.sh/b/129891",
    }
    data.update(overrides)
    return data


async def _fake_gen(data):
    from io import BytesIO
    return BytesIO(b"fake-png")


async def test_reply_map_edits_the_card_in_place():
    reply = _reply()
    edits = []

    async def answer_photo(*a, **k):
        raise AssertionError("reply form must edit, not post a new card")

    message = SimpleNamespace(reply_to_message=reply, message_thread_id=None,
                              from_user=SimpleNamespace(id=1), answer_photo=answer_photo)

    async def fake_build(ref, accuracy, mods_str, api, lang="en"):
        return _whatif_data(accuracy=accuracy, mods=mods_str)

    async def fake_safe_edit(msg, **kwargs):
        edits.append(msg)
        return True

    with patch.object(w, "get_real_reply", return_value=reply), \
         patch.object(w, "get_message_context", return_value={"beatmap_id": 129891, "beatmapset_id": 39804}), \
         patch.object(w, "_build_whatif_data", fake_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async", _fake_gen), \
         patch.object(w, "safe_edit_media", fake_safe_edit):
        await w._handle_whatif(message, "80 ez", osu_api_client=SimpleNamespace())

    assert edits == [reply]  # edited the replied-to card, posted nothing new


async def test_reply_map_falls_back_to_new_card_when_edit_fails():
    reply = _reply()
    posted = []

    async def answer_photo(photo, **k):
        sent = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=9)
        posted.append(sent)
        return sent

    message = SimpleNamespace(reply_to_message=reply, message_thread_id=None,
                              from_user=SimpleNamespace(id=1), answer_photo=answer_photo)

    async def fake_build(ref, accuracy, mods_str, api, lang="en"):
        return _whatif_data(accuracy=accuracy, mods=mods_str)

    async def failing_edit(msg, **kwargs):
        raise RuntimeError("message can't be edited")

    remembered = []

    with patch.object(w, "get_real_reply", return_value=reply), \
         patch.object(w, "get_message_context", return_value={"beatmap_id": 129891, "beatmapset_id": 39804}), \
         patch.object(w, "_build_whatif_data", fake_build), \
         patch.object(w.card_renderer, "generate_whatif_card_async", _fake_gen), \
         patch.object(w, "safe_edit_media", failing_edit), \
         patch.object(w, "remember_message_context", lambda *a, **k: remembered.append(a)):
        await w._handle_whatif(message, "80 ez", osu_api_client=SimpleNamespace())

    assert len(posted) == 1        # fell back to a fresh card
    assert len(remembered) == 1    # and recorded its context for future replies
