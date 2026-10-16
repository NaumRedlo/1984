import asyncio
import types as pytypes

from bot.handlers.profile import recent
from services.render_farm import orders


class _Client:
    def __init__(self, replay):
        self.replay, self.asked = replay, []

    async def download_replay(self, score_id, oauth_token=None):
        self.asked.append((score_id, oauth_token))
        return self.replay


class _Callback:
    def __init__(self, data):
        self.data = data
        self.from_user = pytypes.SimpleNamespace(id=5)
        self.message = pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(type="group", id=1))
        self.bot = object()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


def _run(monkeypatch, tmp_path, replay):
    seen = {}

    async def order(bot, message, person, fetch, lang, client=None, missing="farm.unreadable"):
        path = str(tmp_path / "replay.osr")
        seen["fetched"] = await fetch(path)
        seen["missing"] = missing
        seen["bytes"] = open(path, "rb").read() if seen["fetched"] else None

    async def no_token(_):
        return None

    async def english(_):
        return "EN"

    monkeypatch.setattr(orders, "order", order)
    monkeypatch.setattr(recent, "get_valid_token", no_token)
    monkeypatch.setattr(recent, "get_language", english)
    client = _Client(replay)
    asyncio.run(recent.render_recent(_Callback("rrs:4242"), osu_api_client=client))
    return seen, client


def test_the_render_button_hands_the_downloaded_replay_to_the_farm(monkeypatch, tmp_path):
    seen, client = _run(monkeypatch, tmp_path, b"osr" * 40)
    assert client.asked == [(4242, None)]
    assert seen["fetched"] and seen["bytes"] == b"osr" * 40
    assert seen["missing"] == "rs.render_no_replay"


def test_without_a_replay_from_osu_the_farm_is_told_nothing_came(monkeypatch, tmp_path):
    seen, _ = _run(monkeypatch, tmp_path, None)
    assert seen["fetched"] is False
