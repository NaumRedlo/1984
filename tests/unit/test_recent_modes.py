import pytest
from unittest.mock import AsyncMock

from bot.handlers.profile import recent as handler
from services.image.render import recent as render
from utils.osu import rulesets
from utils.osu.api_client import OsuApiClient
from utils.osu.resolve_user import resolve_osu_user


def test_a_mode_is_read_from_the_first_or_last_word():
    assert rulesets.split_args("taiko") == (1, "")
    assert rulesets.split_args("m cookiezi") == (3, "cookiezi")
    assert rulesets.split_args("cookiezi ctb") == (2, "cookiezi")
    assert rulesets.split_args("cookiezi") == (None, "cookiezi")
    assert rulesets.split_args("") == (None, "")
    assert rulesets.split_args("-t") == (1, "")


def test_the_mode_of_a_score_is_where_it_was_played_not_the_maps_own():
    convert = {"ruleset_id": 3, "beatmap": {"mode": "osu"}}
    assert rulesets.of_score(convert) == 3
    assert rulesets.of_score({"beatmap": {"mode": "taiko"}}) == 1
    assert rulesets.of_score({}) == 0


class _Osu:
    def __init__(self, plays):
        self.plays, self.asked = plays, []

    async def get_user_recent_scores(self, osu_id, limit=1, oauth_token=None, mode=None):
        self.asked.append((mode, limit))
        found = self.plays.get(mode, [])
        if isinstance(found, Exception):
            raise found
        return found


async def test_with_no_mode_the_newest_play_of_any_mode_is_shown():
    osu = _Osu({
        "osu": [{"id": 1, "ended_at": "2026-09-24T10:00:00Z"}, {"id": 2, "ended_at": "2026-09-24T09:00:00Z"}],
        "mania": [{"id": 3, "ended_at": "2026-09-24T11:00:00Z"}],
        "taiko": [{"id": 4, "ended_at": "2026-09-01T11:00:00Z"}],
    })
    standard, shown = await handler.fetch_recent(osu, 7, None)
    assert shown["id"] == 3
    assert [s["id"] for s in standard] == [1, 2]
    assert dict(osu.asked) == {"osu": 50, "taiko": 1, "fruits": 1, "mania": 1}


async def test_a_named_mode_is_the_only_one_asked_and_nothing_else_is_counted():
    osu = _Osu({"taiko": [{"id": 4, "ended_at": "2026-09-01T11:00:00Z"}]})
    standard, shown = await handler.fetch_recent(osu, 7, None, 1)
    assert shown["id"] == 4 and standard == []
    assert osu.asked == [("taiko", 1)]


async def test_no_plays_at_all():
    assert await handler.fetch_recent(_Osu({}), 7, None) == ([], None)


@pytest.mark.parametrize("plays", [
    {"osu": TimeoutError("osu unavailable")},
    {"osu": [{"id": 1}], "mania": TimeoutError("mania unavailable")},
])
async def test_failed_requests_are_not_reported_as_no_plays_or_an_older_play(plays):
    with pytest.raises(TimeoutError):
        await handler.fetch_recent(_Osu(plays), 7, None)


async def test_recent_dates_are_compared_in_utc():
    osu = _Osu({
        "osu": [{"id": 1, "ended_at": "2026-10-04T22:00:00+03:00"}],
        "mania": [{"id": 2, "created_at": "2026-10-04T20:00:00Z"}],
    })
    _, shown = await handler.fetch_recent(osu, 7, None)
    assert shown["id"] == 2


async def test_rejected_user_token_retries_recent_with_public_access():
    client = OsuApiClient()
    client._make_request = AsyncMock(side_effect=[None, [{"id": 7}]])
    assert await client.get_user_recent_scores(123, 50, "rejected", "osu") == [{"id": 7}]
    first, second = client._make_request.call_args_list
    assert first.kwargs["bearer_token"] == "rejected"
    assert "bearer_token" not in second.kwargs
    assert first.args == second.args == ("GET", "users/123/scores/recent")
    assert first.kwargs["params"] == second.kwargs["params"] == {"limit": 50, "include_fails": 1, "mode": "osu"}


@pytest.mark.parametrize("reply", [None, {}, {"error": "unavailable"}])
async def test_invalid_recent_response_is_not_an_empty_history(reply):
    client = OsuApiClient()
    client._make_request = AsyncMock(return_value=reply)
    with pytest.raises(ValueError, match="Invalid recent scores response"):
        await client.get_user_recent_scores(123)


async def test_real_empty_recent_history_does_not_trigger_a_retry():
    client = OsuApiClient()
    client._make_request = AsyncMock(return_value=[])
    assert await client.get_user_recent_scores(123, oauth_token="valid") == []
    client._make_request.assert_awaited_once()


@pytest.mark.parametrize("name", ["nazeetskyyy", "1011", "-legusshhka-"])
async def test_lookup_keeps_numeric_and_punctuated_names_as_usernames(name):
    client = OsuApiClient()
    client._make_request = AsyncMock(return_value={
        "id": 123, "username": name, "statistics": None, "cover": None, "country": None,
    })
    user = await resolve_osu_user(client, name)
    assert user["id"] == 123 and user["username"] == name
    call = client._make_request.call_args
    assert call.args == ("GET", f"users/{name}/osu")
    assert call.kwargs["params"] == {"key": "username"}
    assert call.kwargs["strict"] is True


@pytest.mark.parametrize("ruleset, stats, labels, values", [
    (1, {"great": 900, "ok": 30, "miss": 2}, ["great", "good", "miss"], [900, 30, 2]),
    (2, {"great": 800, "large_tick_hit": 90, "small_tick_hit": 300, "miss": 4, "large_tick_miss": 1},
     ["fruits", "drops", "droplets", "miss"], [800, 90, 300, 5]),
    (3, {"perfect": 1000, "great": 200, "good": 20, "ok": 5, "meh": 1, "miss": 3},
     ["max", "300", "200", "100", "50", "miss"], [1000, 200, 20, 5, 1, 3]),
    (0, {"great": 500, "ok": 3, "meh": 1, "miss": 2}, ["300", "100", "50", "miss"], [500, 3, 1, 2]),
])
def test_each_mode_shows_its_own_judgements(ruleset, stats, labels, values):
    counts = render._hit_counts(ruleset, stats)
    assert [c[0] for c in counts] == labels and [c[1] for c in counts] == values


def test_each_mode_shows_the_settings_it_is_played_by():
    beatmap = {"accuracy": 8.0, "drain": 6.0, "cs": 4.0, "mode": "osu"}
    adjusted = {"cs": 5.2, "ar": 10.0, "od": 9.0, "hp": 8.0}
    taiko = render._details(1, beatmap, ["HR", "DT"], adjusted, None)
    assert [d[0] for d in taiko] == ["OD", "HP"] and taiko[0][2] == pytest.approx(10.0)
    assert [d[0] for d in render._details(2, beatmap, [], adjusted, None)] == ["CS", "AR", "HP"]
    mania = render._details(3, beatmap, [], adjusted, 7)
    assert mania[0][:3] == ("KEYS", "cs", 7.0) and mania[0][3] == 0
    assert render._details(3, {**beatmap, "mode": "mania", "cs": 4}, [], adjusted, None)[0][2] == 4.0


def test_a_failed_play_is_measured_against_the_whole_map():
    raw = {"statistics": {"great": 300, "miss": 20}, "maximum_statistics": {"great": 640}}
    assert render._judged_fraction(raw) == pytest.approx(0.5)
    assert render._judged_fraction({"statistics": {"great": 1}}) is None


def test_the_mode_marks_are_drawn():
    for ruleset in range(4):
        icon = render._ruleset_icon(ruleset, 22, (255, 0, 0))
        assert icon.size == (22, 22) and icon.getbbox() is not None
