"""The `map` command's argument parsing and handler
(bot/handlers/maplink/whatif.py). Direct calls with SimpleNamespace messages,
mirroring test_render_osr_confirm.py's style."""

from types import SimpleNamespace
from unittest.mock import patch

from bot.filters import TriggerArgs
from bot.handlers.maplink import whatif as w


def _msg(reply=None, thread_id=None):
    return SimpleNamespace(reply_to_message=reply, message_thread_id=thread_id)


def _reply(text=None, caption=None, chat_id=1, message_id=5):
    return SimpleNamespace(
        text=text, caption=caption, forum_topic_created=None,
        message_id=message_id, chat=SimpleNamespace(id=chat_id),
    )


def test_link_and_accuracy_and_mods_in_one_line():
    ta = TriggerArgs("map", "https://osu.ppy.sh/beatmaps/129891 94 hr", "map ...")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert err is None
    assert parsed.beatmap_ref.beatmap_id == 129891
    assert parsed.accuracy == 94.0
    assert parsed.mods_str == "HR"


def test_reply_only_form_reads_link_from_reply_text():
    reply = _reply(text="check this out https://osu.ppy.sh/beatmaps/129891")
    ta = TriggerArgs("map", "94 hr", "map 94 hr")
    parsed, err = w._parse_whatif_args(ta, _msg(reply=reply))
    assert err is None
    assert parsed.beatmap_ref.beatmap_id == 129891
    assert parsed.accuracy == 94.0


def test_reply_to_bot_card_falls_back_to_message_context():
    """Replying to the bot's OWN rendered map card: its caption carries no
    raw URL, only a button — resolved via get_message_context instead."""
    reply = _reply(text=None, caption="FREEDOM DiVE [FOUR DIMENSIONS]")
    ta = TriggerArgs("map", "94 hr", "map 94 hr")
    with patch.object(w, "get_message_context",
                      return_value={"beatmap_id": 129891, "beatmapset_id": 39804}):
        parsed, err = w._parse_whatif_args(ta, _msg(reply=reply))
    assert err is None
    assert parsed.beatmap_ref.beatmap_id == 129891
    assert parsed.beatmap_ref.beatmapset_id == 39804


def test_no_link_anywhere_shows_usage():
    ta = TriggerArgs("map", "94 hr", "map 94 hr")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert parsed is None
    assert "Использование" in err


def test_missing_accuracy_is_an_error():
    ta = TriggerArgs("map", "https://osu.ppy.sh/beatmaps/129891", "map ...")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert parsed is None
    assert "точность" in err.lower()


def test_invalid_accuracy_text_is_an_error():
    ta = TriggerArgs("map", "https://osu.ppy.sh/beatmaps/129891 abc", "map ...")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert parsed is None
    assert "Некорректная точность" in err


def test_accuracy_out_of_range_is_an_error():
    ta = TriggerArgs("map", "https://osu.ppy.sh/beatmaps/129891 150", "map ...")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert parsed is None
    assert "0" in err and "100" in err


def test_unknown_mod_is_an_error():
    ta = TriggerArgs("map", "https://osu.ppy.sh/beatmaps/129891 94 xy", "map ...")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert parsed is None
    assert "мод" in err.lower()


def test_comma_decimal_accuracy_parses():
    ta = TriggerArgs("map", "https://osu.ppy.sh/beatmaps/129891 94,5", "map ...")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert err is None
    assert parsed.accuracy == 94.5


def test_percent_sign_in_accuracy_parses():
    ta = TriggerArgs("map", "https://osu.ppy.sh/beatmaps/129891 94%", "map ...")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert err is None
    assert parsed.accuracy == 94.0


def test_nomod_is_valid():
    ta = TriggerArgs("map", "https://osu.ppy.sh/beatmaps/129891 98", "map ...")
    parsed, err = w._parse_whatif_args(ta, _msg())
    assert err is None
    assert parsed.mods_str == ""
