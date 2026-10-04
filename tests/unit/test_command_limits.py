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


def callback(user_id=7, action="pg|list|7|2"):
    return CallbackQuery(id="click", from_user=message(user_id).from_user, chat_instance="chat", data=action)


def command(name="rs", args=""):
    return {"trigger_args": SimpleNamespace(trigger=name, args=args)}


def marked(group):
    return {"handler": SimpleNamespace(flags={"rate_limit": group})}


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rate, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(rate, "get_language", AsyncMock(return_value="RU"))
    monkeypatch.setattr(Message, "answer", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    return now


async def test_heavy_request_blocks_another_heavy_request_across_chats_but_leaves_menu_and_close_available(clock):
    middleware = rate.RateLimitMiddleware()
    started, release = asyncio.Event(), asyncio.Event()

    async def work(event, data):
        started.set()
        await release.wait()
        return "done"

    handler = AsyncMock(side_effect=work)
    first = asyncio.create_task(middleware(handler, message(), command()))
    await started.wait()
    clock[0] = 20
    menu = AsyncMock(return_value="menu")
    try:
        assert await middleware(handler, message(chat_id=-200), command()) is None
        assert handler.await_count == 1
        assert "ещё выполняется" in Message.answer.call_args.args[0]
        assert await middleware(menu, callback(), {}) == "menu"
        assert await middleware(menu, callback(action="st:close"), {}) == "menu"
    finally:
        release.set()
        assert await first == "done"
    assert await middleware(handler, message(), command()) == "done"


async def test_different_players_can_run_commands_at_the_same_time(clock):
    middleware = rate.RateLimitMiddleware()
    started, release = asyncio.Event(), asyncio.Event()

    async def work(event, data):
        if event.from_user.id == 7:
            started.set()
            await release.wait()
        return event.from_user.id

    first = asyncio.create_task(middleware(work, message(), command()))
    await started.wait()
    try:
        assert await middleware(work, message(8), command()) == 8
    finally:
        release.set()
        assert await first == 7


async def test_menu_and_heavy_budgets_are_separate_and_denied_clicks_do_not_extend_wait(clock):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    await middleware(handler, message(), command())
    clock[0] = 0.1
    await middleware(handler, message(text="/help@MyBot"), {})
    assert handler.await_count == 2
    await middleware(handler, message(), command())
    assert handler.await_count == 2
    assert "через 2 с" in Message.answer.call_args.args[0]
    clock[0] = 1.5
    await middleware(handler, message(), command())
    assert handler.await_count == 3


@pytest.mark.parametrize("group", list(rate.POLICIES))
async def test_each_category_enforces_its_own_burst_window(clock, group):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    policy = rate.POLICIES[group]
    for n in range(policy.count + 1):
        clock[0] = n * (policy.interval + 0.01)
        await middleware(handler, message(), marked(group))
    assert handler.await_count == policy.count
    clock[0] = policy.window
    await middleware(handler, message(), marked(group))
    assert handler.await_count == policy.count + 1


async def test_rejections_do_not_flood_chat_and_always_acknowledge_buttons(clock):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    await middleware(handler, message(), command())
    for _ in range(8):
        await middleware(handler, message(), command())
    assert handler.await_count == 1 and Message.answer.await_count == 1
    for _ in range(4):
        await middleware(handler, callback(action="wif:123"), {})
    assert CallbackQuery.answer.await_count == 4
    assert handler.await_count == 1


@pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_failure_and_cancellation_release_both_local_and_global_capacity(clock, error):
    middleware = rate.RateLimitMiddleware()
    with pytest.raises(type(error)):
        await middleware(AsyncMock(side_effect=error), message(), command())
    assert not middleware._running and middleware._heavy_running == 0
    clock[0] = 1.5
    handler = AsyncMock(return_value="recovered")
    assert await middleware(handler, message(), command()) == "recovered"


async def test_server_capacity_rejects_extra_work_without_blocking_menu_or_consuming_budget(clock, monkeypatch):
    monkeypatch.setattr(rate, "MAX_HEAVY_RUNNING", 1)
    middleware = rate.RateLimitMiddleware()
    started, release = asyncio.Event(), asyncio.Event()

    async def work(event, data):
        started.set()
        await release.wait()

    first = asyncio.create_task(middleware(work, message(), command()))
    await started.wait()
    other = AsyncMock(return_value="ok")
    try:
        assert await middleware(other, message(8), command()) is None
        assert "Бот занят" in Message.answer.call_args.args[0]
        assert await middleware(other, callback(8), {}) == "ok"
    finally:
        release.set()
        await first
    assert await middleware(other, message(8), command()) == "ok"


async def test_mutations_are_serialised_separately_from_navigation(clock):
    middleware = rate.RateLimitMiddleware()
    started, release = asyncio.Event(), asyncio.Event()

    async def work(event, data):
        started.set()
        await release.wait()

    first = asyncio.create_task(middleware(work, callback(action="st:tt:set:hero"), {}))
    await started.wait()
    other = AsyncMock()
    try:
        await middleware(other, callback(action="st:skin:0"), {})
        other.assert_not_awaited()
        await middleware(other, callback(action="st:home"), {})
        other.assert_awaited_once()
    finally:
        release.set()
        await first


async def test_close_is_not_blocked_by_exhausted_menu_budget_and_noops_do_not_run_handlers(clock):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    policy = rate.POLICIES["menu"]
    for n in range(policy.count):
        clock[0] = n * 0.31
        await middleware(handler, callback(), {})
    await middleware(handler, callback(action="st:close"), {})
    assert handler.await_count == policy.count + 1
    await middleware(handler, callback(action="pg|noop"), {})
    assert handler.await_count == policy.count + 1
    CallbackQuery.answer.assert_awaited_once()


async def test_regular_messages_are_ignored_but_registered_links_and_uploads_are_limited(clock):
    middleware, handler = rate.RateLimitMiddleware(), AsyncMock()
    for _ in range(12):
        await middleware(handler, message(text="hello"), {})
        await middleware(handler, message(text=None), {})
    assert handler.await_count == 24
    await middleware(handler, message(text="https://osu.ppy.sh/scores/123"), marked("heavy"))
    await middleware(handler, message(text="https://osu.ppy.sh/beatmaps/123"), marked("heavy"))
    assert handler.await_count == 25
    await middleware(handler, message(text=None), marked("upload"))
    await middleware(handler, message(text=None), marked("upload"))
    assert handler.await_count == 26


@pytest.mark.parametrize(("action", "group"), [
    ("st:skin", "menu"), ("st:skin:1", "mutation"), ("st:tt:pg:2", "menu"),
    ("st:tt:set:hero", "mutation"), ("lbm:123", "heavy"), ("wif:123", "heavy"),
    ("tpp|open|7|8", "heavy"), ("ap:r:rf", "heavy"), ("st:close", "close"),
])
def test_buttons_use_the_cost_of_their_action(action, group):
    assert rate.category(callback(action=action), {}) == group


def test_registered_automatic_handlers_have_explicit_policies():
    from bot.handlers.farm import router as farm
    from bot.handlers.maplink.handlers import router as maps
    from bot.handlers.maplink.whatif import router as whatif
    from bot.handlers.scorelink.handlers import router as scores

    for router in (maps, scores):
        assert all(handler.flags["rate_limit"] == "heavy" for handler in router.message.handlers)
    assert all(handler.flags["rate_limit"] == "upload" for handler in farm.message.handlers)
    bare = next(handler for handler in whatif.message.handlers if handler.callback.__name__ == "cmd_whatif_bare")
    assert bare.flags["rate_limit"] == "heavy"
