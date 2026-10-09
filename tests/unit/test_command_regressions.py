from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage, SendPhoto, DeleteMessage, EditMessageText
from aiogram.types import Message, Update, Chat, User, CallbackQuery

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.errors import on_error
from bot.handlers.profile import recent, top_play, top_plays, update
from bot.handlers.profile.targets import Target
from services.command_refresh import RefreshRequests
from tests.unit.test_live_tracker import database, fresh_presence
from tests.unit.test_recent_feed import setup, rs
from utils.i18n import t


def incoming(text="rs osu", mid=1, chat=-1):
    return Message(message_id=mid, date=datetime.now(timezone.utc),
                   chat=Chat(id=chat, type="supergroup"),
                   from_user=User(id=7, first_name="Naum", is_bot=False), text=text)


class Transport(BaseSession):
    def __init__(self, failure):
        super().__init__()
        self.failure, self.calls, self.deleted = failure, [], set()

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, DeleteMessage):
            if self.failure == "delete":
                raise TelegramBadRequest(method=method, message="message can't be deleted")
            self.deleted.add(method.message_id)
            return True
        if isinstance(method, SendPhoto) and self.failure == "upload":
            raise TelegramNetworkError(method=method, message="upload unavailable")
        if isinstance(method, EditMessageText) and method.message_id in self.deleted:
            raise TelegramBadRequest(method=method, message="message to edit not found")
        return incoming(mid=1000 + len(self.calls)).as_(bot)


@pytest.mark.parametrize("command", ["rs", "tp", "upd"])
@pytest.mark.parametrize("failure", [None, "upload", "delete"])
async def test_commands_deliver_before_deleting_status_and_report_upload_errors(database, monkeypatch, command, failure):
    _, client, _, _ = await setup(database, monkeypatch)
    client.get_user_best_scores = AsyncMock(return_value=client.scores)
    client.get_user_data = AsyncMock(return_value={"id": 70, "username": "Naum", "playmode": "osu", "pp": 100, "accuracy": 99, "play_count": 20})
    for module in (top_play, update):
        monkeypatch.setattr(module, "get_language", AsyncMock(return_value="en"))
        monkeypatch.setattr(module, "resolve_target", AsyncMock(return_value=Target(70, "Naum")))
        monkeypatch.setattr(module, "token_for", AsyncMock(return_value=None))
    monkeypatch.setattr(top_play, "build_recent_card_data", recent.build_recent_card_data)
    monkeypatch.setattr(update, "refresh_requests", RefreshRequests())
    monkeypatch.setattr(update.card_renderer, "generate_update_card_async", AsyncMock(side_effect=lambda data: BytesIO(b"png")))
    handler = {"rs": recent.cmd_recent, "tp": top_play.cmd_top_play, "upd": update.cmd_update}[command]
    transport = Transport(failure)
    bot = Bot("123456:TEST", session=transport)
    dispatcher, router = Dispatcher(), Router()
    router.message.register(handler, TextTriggerFilter(command))
    router.errors.register(on_error)
    dispatcher.include_router(router)
    try:
        await dispatcher.feed_update(bot, Update(update_id=1, message=incoming(command)), osu_api_client=client, tenant_chat_id=-1)
        assert sum(isinstance(call, SendMessage) for call in transport.calls) == 1
        photos = [n for n, call in enumerate(transport.calls) if isinstance(call, SendPhoto)]
        assert len(photos) == 1
        deletes = [n for n, call in enumerate(transport.calls) if isinstance(call, DeleteMessage)]
        edits = [call for call in transport.calls if isinstance(call, EditMessageText)]
        if failure == "upload":
            assert not deletes
            assert len(edits) == 1 and edits[0].message_id not in transport.deleted
        else:
            assert len(deletes) == 1 and deletes[0] > photos[0]
            assert not edits
    finally:
        await bot.session.close()


@pytest.mark.parametrize("text", [None, "", " ", "\t\n", "\u2003"])
async def test_empty_text_is_ignored(text):
    assert await TextTriggerFilter("rs")(incoming(text)) is False


@pytest.mark.parametrize("argument", ["0", "#0", "\\0", "101"])
async def test_invalid_top_place_is_rejected_before_resolving_a_player(monkeypatch, argument):
    monkeypatch.setattr(top_play, "get_language", AsyncMock(return_value="en"))
    target = AsyncMock()
    monkeypatch.setattr(top_play, "resolve_target", target)
    status = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=7))
    await top_play.cmd_top_play(status, TriggerArgs("tp", argument, f"tp {argument}"), object(), -1)
    target.assert_not_awaited()
    assert t("tp.bad_place", "en") in status.answer.call_args.args[0]


@pytest.mark.parametrize("passed,perfect,misses,fc", [(False, True, 0, False), (True, False, 0, False), (True, None, 0, False), (True, True, 0, True), (True, True, 1, False)])
async def test_recent_fallback_only_claims_confirmed_full_combo(database, monkeypatch, passed, perfect, misses, fc):
    _, client, message, _ = await setup(database, monkeypatch)
    score = client.scores[0]
    score.update(passed=passed, is_perfect_combo=perfect)
    score["statistics"]["count_miss"] = misses
    monkeypatch.setattr(recent.card_renderer, "generate_recent_card_async", AsyncMock(side_effect=RuntimeError("renderer unavailable")))
    await rs(message, client)
    text = message.answer.return_value.edit_text.call_args.args[0]
    assert (t("rs.fc", "en") in text) == fc
    if not fc:
        assert t("rs.misses", "en", n=misses) in text


async def test_each_top_plays_message_keeps_its_own_player_and_owner(monkeypatch):
    monkeypatch.setattr(top_plays, "get_language", AsyncMock(return_value="en"))
    rendered = AsyncMock()
    monkeypatch.setattr(top_plays, "_render", rendered)
    a, b = {"username": "Alice"}, {"username": "Bob"}
    first = top_plays._store_nav(7, a)
    second = top_plays._store_nav(7, b)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    def click(key, user=7, mid=1, chat=-1):
        return CallbackQuery(id="click", from_user=User(id=user, first_name="Player", is_bot=False), chat_instance="chat", message=incoming(mid=mid, chat=chat), data=f"tpp|p|{user}|1|{key}")
    await top_plays.on_tpp_page(click(first))
    await top_plays.on_tpp_page(click(second, mid=2, chat=-2))
    await top_plays.on_tpp_page(click(first))
    assert [call.args[3]["username"] for call in rendered.call_args_list] == ["Alice", "Bob", "Alice"]
    rendered.reset_mock()
    await top_plays.on_tpp_page(click(first, user=8))
    rendered.assert_not_awaited()
    top_plays._NAV_CACHE.pop((7, first))
    await top_plays.on_tpp_page(click(first))
    await top_plays.on_tpp_page(click(""))
    rendered.assert_not_awaited()
    keyboard = top_plays._tp_keyboard(7, 0, 2, show_back=False, nav_id=second)
    buttons = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"tpp|p|7|1|{second}" in buttons
    assert all(len(button.encode()) <= 64 for button in buttons)
    top_plays._NAV_CACHE.pop((7, second))
