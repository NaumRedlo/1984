import asyncio

from services.image.core import CardRenderer
from services.teams import team_card_data
from utils.osu.api_client import team_of


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, statement):
        return _Rows(self.rows)


def _team():
    return {
        "id": 970, "name": "karmamafia", "short_name": "PRO", "flag_url": None, "cover_url": None,
        "is_open": True, "empty_slots": 1,
        "statistics": {"rank": 1, "performance": 395316, "ranked_score": 6528305364239, "play_count": 23023592},
        "leader": {"id": 1, "username": "lead", "country": {"code": "NL", "name": "Netherlands"}},
        "members": [
            {"id": 2, "username": "far_away", "country": {"code": "JP", "name": "Japan"}},
            {"id": 3, "username": "here_low", "country": {"code": "RU", "name": "Russia"}},
            {"id": 4, "username": "here_high", "country": {"code": "RU", "name": "Russia"}},
        ],
    }


def test_a_team_is_read_only_when_it_has_an_id_and_a_name():
    assert team_of({"id": 970, "name": "karmamafia", "short_name": "PRO", "flag_url": "f"}) == {
        "id": 970, "name": "karmamafia", "short_name": "PRO", "flag_url": "f"}
    assert team_of(None) is None and team_of({"id": 1}) is None and team_of("team") is None


def test_the_leader_comes_first_then_this_chat_by_pp_then_the_rest():
    session = _Session([(3, 4120, 51200), (4, 8214, 9120)])
    data = asyncio.run(team_card_data(session, _team(), -1001, "ru"))
    assert [m["username"] for m in data["members"]] == ["lead", "here_high", "here_low", "far_away"]
    assert data["members"][0]["is_leader"] and not data["members"][0].get("in_chat")
    assert data["members"][1]["in_chat"] and data["members"][1]["pp"] == 8214
    assert data["team"]["member_count"] == 4 and data["members"][3]["country"] == "jp"


def test_the_team_card_draws_without_any_pictures():
    data = asyncio.run(team_card_data(_Session([(4, 8214, 9120)]), _team(), -1001, "en"))
    png = CardRenderer().generate_team_card(data, {}).getvalue()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
