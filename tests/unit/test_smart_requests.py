import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import command_refresh as refresh
from utils.osu.api_client import OsuApiClient
from utils.singleflight import SingleFlight


async def test_shared_requests_survive_one_cancelled_waiter_and_return_independent_results():
    flight = SingleFlight()
    started, release = asyncio.Event(), asyncio.Event()

    async def load():
        started.set()
        await release.wait()
        return {"scores": [{"pp": 123}]}

    factory = AsyncMock(side_effect=load)
    first = asyncio.create_task(flight.run("player", factory))
    await started.wait()
    second = asyncio.create_task(flight.run("player", factory))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    result = await second
    assert result == {"scores": [{"pp": 123}]}
    factory.assert_awaited_once()
    assert not flight._tasks
    assert await flight.run("player", factory) == result
    assert factory.await_count == 2


async def test_coalesced_results_do_not_share_mutable_objects():
    flight = SingleFlight()
    release = asyncio.Event()

    async def load():
        await release.wait()
        return {"mods": ["HD"]}

    first = asyncio.create_task(flight.run("score", load))
    second = asyncio.create_task(flight.run("score", load))
    await asyncio.sleep(0)
    release.set()
    a, b = await asyncio.gather(first, second)
    a["mods"].append("DT")
    assert b["mods"] == ["HD"]


async def test_failed_shared_work_is_removed_and_shutdown_cancels_pending_work():
    flight = SingleFlight()
    with pytest.raises(RuntimeError):
        await flight.run("failed", AsyncMock(side_effect=RuntimeError("upstream")))
    assert await flight.run("failed", AsyncMock(return_value=7)) == 7
    started = asyncio.Event()

    async def wait():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(flight.run("pending", wait))
    await started.wait()
    await flight.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not flight._tasks


async def test_api_merges_only_identical_reads_and_keeps_tokens_modes_and_writes_separate(monkeypatch):
    client = OsuApiClient()
    release = asyncio.Event()
    started = asyncio.Event()

    async def request(*args, **kwargs):
        started.set()
        await release.wait()
        return {"pp": [1]}

    perform = AsyncMock(side_effect=request)
    monkeypatch.setattr(client, "_perform_request", perform)
    calls = [
        client._make_request("GET", "users/1/osu", params={"key": "id", "limit": 1}),
        client._make_request("GET", "users/1/osu", params={"limit": 1, "key": "id"}),
        client._make_request("GET", "users/1/osu", bearer_token="private"),
        client._make_request("GET", "users/1/osu", bearer_token="another-private"),
        client._make_request("GET", "users/1/mania"),
        client._make_request("POST", "users/1/osu"),
        client._make_request("POST", "users/1/osu"),
    ]
    tasks = [asyncio.create_task(call) for call in calls]
    await started.wait()
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)
    assert perform.await_count == 6
    results[0]["pp"].append(2)
    assert results[1]["pp"] == [1]
    assert not client._requests._tasks
    await client.close()


@pytest.fixture
def refresh_clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(refresh, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


async def test_refresh_coalesces_concurrent_work_and_starts_cooldown_after_success(refresh_clock):
    requests = refresh.RefreshRequests()
    started, release = asyncio.Event(), asyncio.Event()

    async def load():
        started.set()
        await release.wait()
        return {"pp": 42}

    factory = AsyncMock(side_effect=load)
    first = asyncio.create_task(requests.run("player", factory))
    await started.wait()
    refresh_clock[0] = 50
    second = asyncio.create_task(requests.run("player", factory))
    await asyncio.sleep(0)
    release.set()
    assert await first == await second == {"pp": 42}
    factory.assert_awaited_once()
    refresh_clock[0] = 55.1
    with pytest.raises(refresh.RefreshCooldown) as caught:
        await requests.run("player", factory)
    assert caught.value.seconds == 25
    refresh_clock[0] = 80
    assert await requests.run("player", factory) == {"pp": 42}
    assert factory.await_count == 2


@pytest.mark.parametrize("result", [False, None, RuntimeError("failed")])
async def test_failed_refreshes_do_not_start_target_cooldown(refresh_clock, result):
    requests = refresh.RefreshRequests()
    failed = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    if isinstance(result, Exception):
        with pytest.raises(type(result)):
            await requests.run("player", failed)
    else:
        assert await requests.run("player", failed) is result
    assert not requests._active
    assert await requests.run("player", AsyncMock(return_value=True)) is True


async def test_refresh_scope_prevents_cross_token_races_without_sharing_private_data(refresh_clock):
    requests = refresh.RefreshRequests()
    started, release = asyncio.Event(), asyncio.Event()

    async def load():
        started.set()
        await release.wait()
        return {"private": True}

    first = asyncio.create_task(requests.run(("player", "token-a"), load, scope="player"))
    await started.wait()
    try:
        with pytest.raises(refresh.RefreshBusy):
            await requests.run(("player", "token-b"), AsyncMock(), scope="player")
        assert await requests.run("other-player", AsyncMock(return_value=7)) == 7
    finally:
        release.set()
        await first
    with pytest.raises(refresh.RefreshCooldown):
        await requests.run(("player", "token-b"), AsyncMock(), scope="player")
    assert not requests._active


async def test_ranked_dates_keep_repeated_query_parameters_through_the_real_request_wrapper(monkeypatch):
    from utils.osu import api_client as api
    from datetime import datetime

    monkeypatch.setattr(api, "_RANKED_DATES", {})
    client = OsuApiClient()
    perform = AsyncMock(return_value={"beatmaps": [
        {"id": 75, "beatmapset": {"ranked_date": "2008-03-01T00:00:00Z"}},
        {"id": 4000000, "beatmapset": {"ranked_date": "2025-01-01T00:00:00Z"}},
    ]})
    monkeypatch.setattr(client, "_perform_request", perform)
    assert await client.ranked_dates([75, 4000000, 75]) == {
        75: datetime(2008, 3, 1), 4000000: datetime(2025, 1, 1),
    }
    assert perform.call_args.kwargs["params"] == [("ids[]", 75), ("ids[]", 4000000)]
    await client.ranked_dates([75, 4000000])
    perform.assert_awaited_once()
    await client.close()


async def test_repeated_parameters_merge_identical_reads_without_merging_different_id_sets(monkeypatch):
    client = OsuApiClient()
    started, release = asyncio.Event(), asyncio.Event()

    async def load(*args, **kwargs):
        started.set()
        await release.wait()
        return {"beatmaps": []}

    perform = AsyncMock(side_effect=load)
    monkeypatch.setattr(client, "_perform_request", perform)
    calls = [
        client._make_request("GET", "beatmaps", params=[("ids[]", 1), ("ids[]", 2)]),
        client._make_request("GET", "beatmaps", params=[("ids[]", 1), ("ids[]", 2)]),
        client._make_request("GET", "beatmaps", params=[("ids[]", 1), ("ids[]", 3)]),
        client._make_request("GET", "beatmaps", params=[("ids[]", 2), ("ids[]", 1)]),
    ]
    tasks = [asyncio.create_task(call) for call in calls]
    await started.wait()
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*tasks)
    assert perform.await_count == 3
    await client.close()
