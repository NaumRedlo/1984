import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from utils.osu import api_client
from utils.osu.api_client import OsuApiClient


class _Reply:
    def __init__(self, status, body=None, headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self):
        return self.body

    async def text(self):
        return ""


class _Session:
    closed = False

    def __init__(self, clock, replies):
        self.clock, self.replies, self.asked = clock, list(replies), []

    def request(self, method, url, headers=None, **_):
        self.asked.append((round(self.clock.at, 3), url.rsplit("/api/v2/", 1)[1], headers["Authorization"]))
        return self.replies.pop(0)


class _Clock:
    def __init__(self):
        self.at, self.slept = 1000.0, []

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.at += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(api_client.asyncio, "sleep", clock.sleep)
    return clock


def _client(clock, replies):
    client = OsuApiClient()
    client._now = lambda: clock.at
    client.token, client.token_expiry = "app", datetime.now(timezone.utc) + timedelta(hours=1)
    client.session = _Session(clock, replies)
    return client


def _refused(seconds=25):
    return _Reply(429, headers={"Retry-After": str(seconds), "X-RateLimit-Limit": "1200", "X-RateLimit-Remaining": "0"})


async def test_a_refused_request_waits_as_long_as_osu_asked_and_then_goes_through(clock):
    client = _client(clock, [_refused(25), _Reply(200, {"id": 7})])
    assert await client._perform_request("GET", "users/7", bearer_token="hers") == {"id": 7}
    first, second = (at for at, _, _ in client.session.asked)
    assert second - first >= 25
    assert second - first < 27


async def test_nothing_else_is_sent_with_that_token_while_the_door_is_closed(clock):
    client = _client(clock, [_refused(30), _Reply(200, {"n": 1}), _Reply(200, {"n": 2}), _Reply(200, {"n": 3})])
    opened = clock.at + 30
    with pytest.raises(Exception):
        await api_client.OsuApiClient._perform_request.__wrapped__(client, "GET", "users/1", bearer_token="hers")
    assert len(client.session.asked) == 1
    await asyncio.gather(*(client._perform_request("GET", f"users/{n}", bearer_token="hers") for n in (1, 2, 3)))
    later = [at for at, _, _ in client.session.asked[1:]]
    assert len(later) == 3 and all(at >= opened for at in later)
    assert all(b - a >= client.RATE_LIMIT_DELAY - 1e-6 for a, b in zip(later, later[1:]))


async def test_another_token_is_not_kept_waiting_by_a_door_closed_for_this_one(clock):
    client = _client(clock, [_refused(30), _Reply(200, {"mine": True}), _Reply(200, {"app": True})])
    started = clock.at
    with pytest.raises(Exception):
        await api_client.OsuApiClient._perform_request.__wrapped__(client, "GET", "users/1", bearer_token="hers")
    assert await client._perform_request("GET", "users/2", bearer_token="his") == {"mine": True}
    assert await client._perform_request("GET", "users/3") == {"app": True}
    assert [who for _, _, who in client.session.asked] == ["Bearer hers", "Bearer his", "Bearer app"]
    assert all(at - started < 1 for at, _, _ in client.session.asked)


async def test_the_refusal_is_written_down_with_what_is_needed_to_find_its_cause(clock, monkeypatch):
    said = []
    monkeypatch.setattr(api_client.logger, "warning", lambda line, *_, **__: said.append(line))
    client = _client(clock, [_Reply(200, {}), _Reply(200, {}), _refused(25)])
    await client._perform_request("GET", "users/1")
    await client._perform_request("GET", "users/2")
    with pytest.raises(Exception):
        await api_client.OsuApiClient._perform_request.__wrapped__(client, "GET", "beatmaps/9/attributes", bearer_token="hers")
    line = next(found for found in said if found.startswith("Rate limited on"))
    assert "beatmaps/9/attributes" in line and "a user token" in line
    assert "closed for 25s" in line and "limit 1200" in line and "remaining 0" in line
    assert "3 requests sent in the last 60s" in line


async def test_requests_older_than_a_minute_are_no_longer_counted(clock):
    client = _client(clock, [_Reply(200, {}), _Reply(200, {})])
    await client._perform_request("GET", "users/1")
    clock.at += 61
    await client._perform_request("GET", "users/2")
    assert len(client._sent) == 1


async def test_an_open_door_costs_nothing_and_the_pace_between_requests_is_kept(clock):
    client = _client(clock, [_Reply(200, {}), _Reply(200, {}), _Reply(200, {})])
    for n in range(3):
        await client._perform_request("GET", f"users/{n}")
    times = [at for at, _, _ in client.session.asked]
    assert [round(b - a, 3) for a, b in zip(times, times[1:])] == [0.2, 0.2]
    assert client._closed_until == {}
