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


def test_the_roster_keeps_the_leader_on_top_and_numbers_the_rest_by_pp():
    from services.teams import roster_page

    stats = {1: {"pp": 100}, 2: {"pp": 9000}, 3: {"pp": 300}, 4: {"pp": 5000}}
    data = asyncio.run(team_card_data(_Session([]), _team(), -1001, "en", stats))
    first = roster_page(data, 0)
    assert [m["username"] for m in first["members"]] == ["lead", "far_away", "here_high", "here_low"]
    assert [m.get("place") for m in first["members"]] == [None, 1, 2, 3]
    assert first["pages"] == 1 and first["first"] == 1 and first["total"] == 4
    assert roster_page(data, 7)["page"] == 0


def test_the_roster_card_draws_a_page():
    from services.teams import roster_page

    data = asyncio.run(team_card_data(_Session([(4, 8214, 9120)]), _team(), -1001, "ru", {2: {"pp": 9000, "accuracy": 98.2}}))
    png = CardRenderer().generate_team_roster(roster_page(data, 0), {}).getvalue()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_mt_shows_only_your_team_and_tm_finds_a_players_team(monkeypatch):
    from types import SimpleNamespace

    from bot.handlers import team as handler

    asked = []

    class _Client:
        async def get_user_data(self, name):
            asked.append(name)
            return {"username": "Someone", "team": {"id": 588}}

    async def photo(client, wanted, ruleset, chat_id, lang):
        asked.append(("team", wanted))
        return None

    async def language(user_id):
        return "en"

    async def registered(session, user_id, chat_id):
        return SimpleNamespace(team_id=970, osu_user_id=1)

    class _Db:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    async def chosen(raw, user_id):
        return None, raw

    sent = []

    class _Message:
        from_user = SimpleNamespace(id=5)

        async def answer(self, text, **kwargs):
            sent.append(text)

    monkeypatch.setattr(handler, "_team_photo", photo)
    monkeypatch.setattr(handler, "get_language", language)
    monkeypatch.setattr(handler, "get_registered_user", registered)
    monkeypatch.setattr(handler, "get_db_session", lambda: _Db())
    monkeypatch.setattr(handler.rulesets, "chosen", chosen)
    asyncio.run(handler._send_team(_Message(), _Client(), -1001, "Someone", own_only=True))
    asyncio.run(handler._send_team(_Message(), _Client(), -1001, "Someone", own_only=False))
    asyncio.run(handler._send_team(_Message(), _Client(), -1001, "@TQ", own_only=False))
    assert asked == [("team", 970), "Someone", ("team", 588), ("team", "TQ")]
