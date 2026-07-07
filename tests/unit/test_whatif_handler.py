"""The `map` command's argument parsing (bot/handlers/maplink/whatif.py).
Direct calls with SimpleNamespace messages, mirroring
test_render_osr_confirm.py's style.

The beatmap is ONLY ever resolved via context the bot already recorded for
the replied-to message (remember_message_context/get_message_context) —
there is no self-contained "map <link> <accuracy>" form and no parsing of
a raw link out of the reply's own text/caption."""

from types import SimpleNamespace
from unittest.mock import patch

from bot.filters import TriggerArgs
from bot.handlers.maplink import whatif as w


def _msg(reply=None, thread_id=None):
    return SimpleNamespace(reply_to_message=reply, message_thread_id=thread_id)


def _reply(chat_id=1, message_id=5):
    return SimpleNamespace(
        text=None, caption=None, forum_topic_created=None,
        message_id=message_id, chat=SimpleNamespace(id=chat_id),
    )


def _parse_with_context(args_text: str, ctx: dict | None):
    ta = TriggerArgs("map", args_text, f"map {args_text}")
    reply = _reply()
    with patch.object(w, "get_message_context", return_value=ctx):
        return w._parse_whatif_args(ta, _msg(reply=reply))


def test_reply_to_bot_card_resolves_via_message_context():
    parsed, err = _parse_with_context("94 hr", {"beatmap_id": 129891, "beatmapset_id": 39804})
    assert err is None
    assert parsed.beatmap_ref.beatmap_id == 129891
    assert parsed.beatmap_ref.beatmapset_id == 39804
    assert parsed.accuracy == 94.0
    assert parsed.mods_str == "HR"


def test_no_reply_shows_usage():
    ta = TriggerArgs("map", "94 hr", "map 94 hr")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert parsed is None
    assert "Ответь" in err


def test_reply_with_no_recorded_context_shows_usage():
    parsed, err = _parse_with_context("94 hr", None)
    assert parsed is None
    assert "Ответь" in err


def test_link_in_args_is_no_longer_parsed_as_the_beatmap():
    """A link typed directly in the command's own args must NOT be treated
    as the beatmap — only reply-context does that now."""
    parsed, err = _parse_with_context("https://osu.ppy.sh/beatmaps/129891 94 hr", None)
    assert parsed is None
    assert "Ответь" in err


def test_missing_accuracy_is_an_error():
    parsed, err = _parse_with_context("", {"beatmap_id": 129891})
    assert parsed is None
    assert "точность" in err.lower()


def test_invalid_accuracy_text_is_an_error():
    parsed, err = _parse_with_context("abc", {"beatmap_id": 129891})
    assert parsed is None
    assert "Некорректная точность" in err


def test_accuracy_out_of_range_is_an_error():
    parsed, err = _parse_with_context("150", {"beatmap_id": 129891})
    assert parsed is None
    assert "0" in err and "100" in err


def test_unknown_mod_is_an_error():
    parsed, err = _parse_with_context("94 xy", {"beatmap_id": 129891})
    assert parsed is None
    assert "мод" in err.lower()


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
