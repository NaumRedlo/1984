import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from bot.middlewares import rate_limit_middleware as rate


def message(user_id=7, chat_id=-100, text="rs"):
    return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type="supergroup"),
                   from_user=User(id=user_id, is_bot=False, first_name="Player"), text=text)


def callback(user_id=7):
    return CallbackQuery(id="click", from_user=message(user_id).from_user, chat_instance="chat", data="page:2")


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rate, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(rate, "get_language", AsyncMock(return_value="RU"))
    monkeypatch.setattr(Message, "answer", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    return now


async def test_a_running_command_cannot_be_started_again_from_another_chat(clock):
    middleware = rate.RateLimitMiddleware()
    started, release = asyncio.Event(), asyncio.Event()

    async def work(event, data):
        started.set()
        await release.wait()
        return "done"

    handler = AsyncMock(side_effect=work)
    first = asyncio.create_task(middleware(handler, message(), {"trigger_args": object()}))
    await started.wait()
    clock[0] = 20
    try:
        assert await middleware(handler, message(chat_id=-200), {"trigger_args": object()}) is None
        assert handler.await_count == 1
        assert "ещё выполняется" in Message.answer.call_args.args[0]
    finally:
        release.set()
        assert await first == "done"
    assert await middleware(handler, message(), {"trigger_args": object()}) == "done"


async def test_different_players_can_run_commands_at_the_same_time(clock):
    middleware = rate.RateLimitMiddleware()
    started, release = asyncio.Event(), asyncio.Event()

    async def work(event, data):
        if event.from_user.id == 7:
            started.set()
            await release.wait()
        return event.from_user.id

    first = asyncio.create_task(middleware(work, message(), {"trigger_args": object()}))
    await started.wait()
    try:
        assert await middleware(work, message(8), {"trigger_args": object()}) == 8
    finally:
        release.set()
        assert await first == 7


async def test_repeated_plain_commands_and_slash_commands_share_a_cooldown(clock):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    await middleware(handler, message(), {"trigger_args": object()})
    clock[0] = 0.1
    await middleware(handler, message(text="/help"), {})
    assert handler.await_count == 1
    assert "слишком часто" in Message.answer.call_args.args[0]
    clock[0] = rate.COMMAND_INTERVAL
    await middleware(handler, message(), {"trigger_args": object()})
    assert handler.await_count == 2


async def test_bursts_are_limited_even_if_each_click_obeys_the_short_cooldown(clock):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    for n in range(rate.MAX_REQUESTS + 1):
        clock[0] = n * 0.7
        await middleware(handler, callback(), {})
    assert handler.await_count == rate.MAX_REQUESTS
    clock[0] = rate.WINDOW_SECONDS + 1
    await middleware(handler, callback(), {})
    assert handler.await_count == rate.MAX_REQUESTS + 1


async def test_spam_does_not_flood_the_chat_and_every_rejected_button_is_acknowledged(clock):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    await middleware(handler, message(), {"trigger_args": object()})
    for _ in range(8):
        await middleware(handler, message(), {"trigger_args": object()})
    assert handler.await_count == 1 and Message.answer.await_count == 1
    for _ in range(4):
        await middleware(handler, callback(), {})
    assert CallbackQuery.answer.await_count == 4


@pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_failed_or_cancelled_commands_release_the_user(clock, error):
    middleware = rate.RateLimitMiddleware()
    with pytest.raises(type(error)):
        await middleware(AsyncMock(side_effect=error), message(), {"trigger_args": object()})
    assert not middleware._running
    clock[0] = rate.COMMAND_INTERVAL
    handler = AsyncMock(return_value="recovered")
    assert await middleware(handler, message(), {"trigger_args": object()}) == "recovered"


async def test_regular_messages_and_file_uploads_do_not_consume_command_limits(clock):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    for _ in range(12):
        await middleware(handler, message(text="hello"), {})
        await middleware(handler, message(text=None), {})
    await middleware(handler, message(), {"trigger_args": object()})
    assert handler.await_count == 25
    assert Message.answer.await_count == 0
