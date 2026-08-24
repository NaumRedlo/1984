"""Settings you type, and the care that has to go with reading a chat.

A row of five percentages could not say 63, took a line and a half of a phone
screen, and grew a button every time somebody wanted a figure it did not have.
The numbers are typed now — which means this bot reads messages, and a bot that
lives in group chats has to be exact about which ones.
"""

import pytest

from bot.handlers.dossier.renders import Choices
from bot.handlers.profile.settings_menu import typed


class TestWhatItWillAccept:
    @pytest.mark.parametrize(
        "written, want",
        [
            ("1600x900", "1600x900"),
            ("1600×900", "1600x900"),
            (" 1600 X 900 ", "1600x900"),
            ("1600*900", "1600x900"),
        ],
    )
    def test_a_size_however_somebody_wrote_it(self, written, want):
        assert typed._size(written) == want

    @pytest.mark.parametrize("bad", ["1601x900", "1600x901"])
    def test_an_odd_side_is_refused(self, bad):
        """Every encoder this feeds wants an even frame, and finding that out
        at the end of a render is finding it out too late."""
        assert typed._size(bad) is None

    @pytest.mark.parametrize("bad", ["100x100", "8000x4000", "hello", "", "1280"])
    def test_a_size_that_is_not_one(self, bad):
        assert typed._size(bad) is None

    @pytest.mark.parametrize("written", ["80", "80%", " 80 % ", "80 percent"])
    def test_punctuation_is_not_an_argument(self, written):
        """Somebody answering "how loud" with "80%" has answered it."""
        assert typed.FIELDS["music"].parse(written) == 80

    def test_a_number_out_of_range_is_refused_rather_than_clamped(self):
        """Clamping would take 500 and quietly store 200, which is a bot
        deciding it knows better without saying so."""
        assert typed.FIELDS["volume"].parse("500") is None
        assert typed.FIELDS["volume"].parse("200") == 200

    def test_the_overall_volume_reaches_past_what_a_half_can(self):
        """Which is the whole point of it: a quiet map on a phone."""
        assert typed.FIELDS["volume"].parse("200") == 200
        assert typed.FIELDS["music"].parse("200") is None


class TestTheRowItDraws:
    def test_it_says_what_the_setting_is_now(self):
        assert typed.value_button(Choices(fps=60), "fps", "en").text.endswith("60 fps")
        assert "×" in typed.value_button(Choices(), "size", "en").text

    def test_a_setting_nobody_chose_says_so_rather_than_showing_a_number(self):
        """`None` is the engine's own figure, and printing 100 back would be
        the menu claiming a choice nobody made."""
        assert "as it comes" in typed.value_button(Choices(), "meter", "en").text

    def test_every_typed_field_is_a_field_choices_has(self):
        for key in typed.FIELDS:
            assert key in Choices.__dataclass_fields__


class TestWhoItListensTo:
    """The careful part. Without the reply the handler would read the chat;
    without the person a passer-by could answer somebody else's settings."""

    class _User:
        def __init__(self, uid):
            self.id = uid

    class _Chat:
        def __init__(self, cid):
            self.id = cid

    class _Msg:
        def __init__(self, *, text="80", chat=1, mid=2, who=7, reply=None):
            self.text = text
            self.chat = TestWhoItListensTo._Chat(chat)
            self.message_id = mid
            self.from_user = TestWhoItListensTo._User(who)
            self.reply_to_message = reply

    @pytest.fixture(autouse=True)
    def _clean(self):
        typed._ASKED.clear()
        yield
        typed._ASKED.clear()

    async def test_a_reply_to_our_prompt_from_the_person_asked(self):
        prompt = self._Msg(chat=1, mid=99)
        typed._ASKED[(1, 99)] = (7, "music")
        answer = self._Msg(who=7, reply=prompt)
        assert await typed.AnswerFilter()(answer) == {"typed_key": "music"}

    async def test_ordinary_chat_falls_straight_through(self):
        typed._ASKED[(1, 99)] = (7, "music")
        assert await typed.AnswerFilter()(self._Msg(reply=None)) is False

    async def test_a_reply_to_something_else_is_not_an_answer(self):
        typed._ASKED[(1, 99)] = (7, "music")
        stranger = self._Msg(chat=1, mid=5)
        assert await typed.AnswerFilter()(self._Msg(reply=stranger)) is False

    async def test_somebody_elses_settings_are_not_yours_to_answer(self):
        prompt = self._Msg(chat=1, mid=99)
        typed._ASKED[(1, 99)] = (7, "music")
        assert await typed.AnswerFilter()(self._Msg(who=8, reply=prompt)) is False

    async def test_an_empty_message_is_not_an_answer(self):
        prompt = self._Msg(chat=1, mid=99)
        typed._ASKED[(1, 99)] = (7, "music")
        assert await typed.AnswerFilter()(self._Msg(text="   ", reply=prompt)) is False
