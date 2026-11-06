import asyncio

from services.leaderboard.marks import mark_rows
from services.render_farm import billing, subscriptions


class _Catalogue:
    async def prices(self):
        return ()


def test_rows_wear_the_mark_their_player_chose_to_show(monkeypatch):
    asked = []

    async def badges(players, prices):
        asked.append(set(players))
        return {7: {"rank": 2, "stage": 3, "title": None}, 8: {"rank": None, "stage": 2, "title": "Lava"}}

    async def titled(players):
        return {7: {"rank": 2, "stage": 3}, 10: {"rank": 1, "stage": 1}}

    monkeypatch.setattr(subscriptions, "badges", badges)
    monkeypatch.setattr(billing, "shared_catalogue", lambda: _Catalogue())
    monkeypatch.setattr(billing, "title_palettes_of", titled)
    rows = [{"player_id": 7}, {"player_id": 8}, {"player_id": 9, "osu_user_id": billing.CREATORS[0]}, {"player_id": 10}]
    asyncio.run(mark_rows(None, rows + [None]))

    assert asked == [{7, 8, 10}]
    assert rows[0]["mark"] == rows[0]["palette"] == {"rank": 2, "stage": 3}
    assert rows[1]["mark"] is None, "a hidden badge leaves the row plain"
    assert rows[2]["mark"] == billing.CREATOR_MARK
    assert rows[3]["mark"] is None
    assert rows[0]["title_palette"] == {"rank": 2, "stage": 3} and rows[1]["title_palette"] is None
    assert rows[3]["title_palette"] == {"rank": 1, "stage": 1}, "a title may wear the plan's colour with the badge hidden"


def test_a_failed_lookup_leaves_the_board_as_it_was(monkeypatch):
    async def badges(players, prices):
        raise RuntimeError("database is away")

    monkeypatch.setattr(subscriptions, "badges", badges)
    monkeypatch.setattr(billing, "shared_catalogue", lambda: _Catalogue())
    rows = [{"player_id": 7}]
    asyncio.run(mark_rows(None, rows))
    assert "mark" not in rows[0]


def test_a_title_takes_the_colour_of_the_plan_only_for_those_who_chose_it(monkeypatch):
    from services.image.render.leaderboard_delta import render_delta_leaderboard

    def row(palette):
        return {"position": 1, "user_id": 1, "username": "a", "title_label": "Доброволец", "title_color": (90, 180, 90),
                "delta_label": "+1", "absolute_label": "1", "movement": 0, "avatar_data": None, "title_palette": palette}

    plain = render_delta_leaderboard({"lang": "ru", "title": "t", "subtitle": "s", "rows": [row(None)], "fmt": {"new": "new"}})
    for stage in (1, 4):
        tinted = render_delta_leaderboard({"lang": "ru", "title": "t", "subtitle": "s", "rows": [row({"rank": 3, "stage": stage})], "fmt": {"new": "new"}})
        assert tinted != plain
