import pytest

from bot.handlers.auth import linked
from services.oauth import server


class Sent:
    def __init__(self, log):
        self.log = log

    async def delete(self):
        self.log.append(("gone",))


class Bot:
    def __init__(self):
        self.log = []

    async def delete_message(self, chat_id, message_id):
        self.log.append(("delete", chat_id, message_id))

    async def send_message(self, chat_id, text, parse_mode=None):
        self.log.append(("send", chat_id, text, parse_mode))
        return Sent(self.log)


@pytest.fixture
def quick(monkeypatch):
    async def language(_telegram_id):
        return "EN"

    monkeypatch.setattr(linked, "get_language", language)
    monkeypatch.setattr(linked, "SHOWN_FOR", 0)


async def test_the_link_message_goes_and_the_notice_shows_then_goes(quick):
    bot = Bot()
    await linked.notifier(bot)(42, "<peppy>", (-100, 7))
    assert bot.log[0] == ("delete", -100, 7)
    kind, chat_id, text, parse_mode = bot.log[1]
    assert (kind, chat_id, parse_mode) == ("send", -100, "HTML")
    assert "&lt;peppy&gt;" in text
    assert bot.log[2] == ("gone",)


async def test_nothing_is_sent_without_a_link_message(quick):
    bot = Bot()
    await linked.notifier(bot)(42, "peppy", None)
    assert bot.log == []


async def test_the_callback_tells_whoever_listens_and_survives_nobody(monkeypatch):
    told = []

    async def listen(telegram_id, osu_username, link_msg):
        told.append((telegram_id, osu_username, link_msg))

    monkeypatch.setattr(server, "_linked", None)
    await server._notify_telegram(1, "a", (2, 3))
    server.set_linked(listen)
    await server._notify_telegram(1, "a", (2, 3))
    server.set_linked(None)
    assert told == [(1, "a", (2, 3))]
