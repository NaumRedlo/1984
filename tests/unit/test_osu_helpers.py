import pytest

from utils.osu.helpers import extract_beatmap_id, remember_message_context, get_message_context


class TestExtractBeatmapId:
    def test_plain_number(self):
        assert extract_beatmap_id("12345") == "12345"

    def test_beatmap_url(self):
        assert extract_beatmap_id("https://osu.ppy.sh/beatmaps/98765") == "98765"

    def test_beatmapset_url(self):
        url = "https://osu.ppy.sh/beatmapsets/111#osu/54321"
        assert extract_beatmap_id(url) == "54321"

    def test_beatmapset_url_no_mode(self):
        url = "https://osu.ppy.sh/beatmapsets/222/33333"
        assert extract_beatmap_id(url) == "33333"

    def test_garbage_returns_none(self):
        assert extract_beatmap_id("no map here") is None

    def test_empty_string(self):
        assert extract_beatmap_id("") is None

    def test_url_with_whitespace(self):
        assert extract_beatmap_id("  12345  ") == "12345"


class TestMessageContext:
    def test_remember_and_get(self):
        ctx = {"beatmap_id": 100, "title": "test map"}
        remember_message_context(1, 10, ctx)
        result = get_message_context(1, 10)
        assert result is ctx

    def test_get_missing_returns_none(self):
        result = get_message_context(999, 999)
        assert result is None

    def test_fallback_to_latest_in_chat(self):
        ctx1 = {"beatmap_id": 200}
        ctx2 = {"beatmap_id": 300}
        remember_message_context(5, 50, ctx1)
        remember_message_context(5, 51, ctx2)
        # запрос по несуществующему message_id — вернёт последний с beatmap_id
        result = get_message_context(5, 999)
        assert result is not None
        assert result["beatmap_id"] == 300
