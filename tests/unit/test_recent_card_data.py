from unittest.mock import patch

from services.image.core import CardRenderer
from services.image.render import recent as recent_render

def _raw_score(**overrides):
    score = {
        "id": 555, "accuracy": 0.99, "passed": True, "rank": "S", "pp": 300.0,
        "max_combo": 720,
        "mods": [{"acronym": "HD"}, {"acronym": "DT"}],
        "statistics": {"great": 550, "ok": 8, "meh": 0, "miss": 0},
        "beatmap": {
            "id": 129891, "version": "FOUR DIMENSIONS", "difficulty_rating": 7.42,
            "cs": 4.0, "ar": 9.0, "accuracy": 9.0, "drain": 8.0, "bpm": 180,
            "total_length": 126, "max_combo": 720, "status": "ranked",
            "count_circles": 500, "count_sliders": 200, "count_spinners": 5,
        },
        "beatmapset": {
            "id": 39804, "artist": "xi", "title": "FREEDOM DiVE",
            "creator": "Nakagawa-Kanon", "user_id": 12345,
        },
        "ended_at": "2026-07-07T00:00:00Z",
    }
    score.update(overrides)
    return score

async def _fake_calculate_pp(**kwargs):
    return {"pp_current": 300.0, "pp_if_fc": 310.0, "pp_if_ss": 320.0,
            "star_rating": 7.9, "max_combo": 720}

async def test_build_recent_card_data_maps_every_field():
    with patch.object(recent_render, "calculate_pp", _fake_calculate_pp):
        data = await recent_render.build_recent_card_data(
            _raw_score(), username="kazaki1865", player_id=999,
            player_cover_url="http://example.com/cover.jpg",
            requester_name="tester", lang="ru",
        )

    expected_keys = {
        "lang", "card_mode", "score_id", "username", "artist", "title", "version",
        "star_rating", "mods", "rank_grade", "accuracy", "combo", "misses", "pp",
        "beatmap_id", "beatmapset_id", "max_combo", "cs", "ar", "od", "hp", "bpm",
        "total_length", "total_score", "score_client", "mapper_name", "mapper_id",
        "player_id", "player_cover_url", "count_300", "count_100", "count_50",
        "pp_if_fc", "pp_if_ss", "requester_name", "beatmap_status", "played_at",
        "passed", "total_objects",
    }
    assert expected_keys <= data.keys()
    assert data["card_mode"] == "recent"
    assert data["artist"] == "xi" and data["title"] == "FREEDOM DiVE"
    assert data["mods"] == "HDDT"
    assert data["star_rating"] == 7.42
    assert data["pp_if_fc"] == 310.0 and data["pp_if_ss"] == 320.0
    assert data["beatmap_id"] == 129891 and data["beatmapset_id"] == 39804
    assert data["mapper_name"] == "Nakagawa-Kanon" and data["mapper_id"] == 12345
    assert data["player_id"] == 999
    assert data["count_300"] == 550

async def test_card_mode_shared_is_threaded_through():
    with patch.object(recent_render, "calculate_pp", _fake_calculate_pp):
        data = await recent_render.build_recent_card_data(
            _raw_score(), username="x", player_id=1, card_mode="shared",
        )
    assert data["card_mode"] == "shared"

async def test_output_renders_end_to_end():
    with patch.object(recent_render, "calculate_pp", _fake_calculate_pp):
        data = await recent_render.build_recent_card_data(
            _raw_score(), username="kazaki1865", player_id=999, lang="en",
        )
    buf = CardRenderer().generate_recent_card(data, None, None, None, None, [0.5] * 64)
    png = buf.getvalue()
    assert png.startswith(b"\x89PNG") and len(png) > 2000

async def test_pp_calculation_failure_falls_back_gracefully():
    async def _raising(**kwargs):
        raise RuntimeError("boom")
    with patch.object(recent_render, "calculate_pp", _raising):
        data = await recent_render.build_recent_card_data(
            _raw_score(), username="x", player_id=1,
        )

    assert data["star_rating"] == 7.42
    assert data["pp"] == 300.0
    assert data["pp_if_fc"] == 0.0 and data["pp_if_ss"] == 0.0

async def test_the_rating_with_mods_is_asked_of_ppy_not_of_rosu():
    class _Client:
        def __init__(self):
            self.asked = []

        async def effective_sr(self, beatmap_id, mods, nominal):
            self.asked.append((beatmap_id, mods, nominal))
            return 10.58

    client = _Client()
    with patch.object(recent_render, "calculate_pp", _fake_calculate_pp):
        data = await recent_render.build_recent_card_data(
            _raw_score(), username="kazaki1865", player_id=999, client=client,
        )
    assert data["star_rating"] == 10.58
    assert client.asked == [(129891, [{"acronym": "HD"}, {"acronym": "DT"}], 7.42)]


class _Best:
    def __init__(self, ids=(), fails=False):
        self.ids, self.fails, self.asked = list(ids), fails, []

    async def effective_sr(self, beatmap_id, mods, nominal):
        return nominal

    async def get_user_best_scores(self, user_id, limit=5, mode="osu", oauth_token=None):
        self.asked.append((user_id, limit, mode))
        if self.fails:
            raise RuntimeError("osu! is away")
        return [{"id": found} for found in self.ids]


async def _built(client, **score):
    mode = score.pop("card_mode", "recent")
    with patch.object(recent_render, "calculate_pp", _fake_calculate_pp):
        return await recent_render.build_recent_card_data(
            _raw_score(**score), username="kazaki1865", player_id=999, client=client, card_mode=mode,
        )


async def test_the_place_is_where_the_score_stands_among_the_players_best():
    client = _Best(ids=[901, 902, 555, 903])
    data = await _built(client)
    assert data["top_place"] == 3
    assert client.asked == [(999, 100, "osu")]


async def test_a_score_that_is_not_among_the_best_has_no_place():
    assert (await _built(_Best(ids=[901, 902])))["top_place"] is None
    assert (await _built(_Best(fails=True)))["top_place"] is None
    assert (await _built(None))["top_place"] is None


async def test_a_failed_or_unweighted_score_is_not_looked_up_at_all():
    for score in ({"passed": False}, {"pp": None}, {"pp": 0}, {"id": None}):
        client = _Best(ids=[555])
        data = await _built(client, **score)
        assert data["top_place"] is None and client.asked == []


async def test_a_top_play_card_already_knows_its_place_and_does_not_ask_again():
    client = _Best(ids=[555])
    data = await _built(client, card_mode="top")
    assert data["top_place"] is None and client.asked == []


async def test_the_best_scores_are_asked_for_in_the_mode_that_was_played():
    client = _Best(ids=[555])
    data = await _built(client, ruleset_id=3)
    assert client.asked == [(999, 100, "mania")] and data["top_place"] == 1


async def test_the_score_and_the_client_come_with_the_card():
    stable = await _built(None, legacy_total_score=1087654321, total_score=987654)
    assert stable["total_score"] == 1087654321 and stable["score_client"] == "stable"
    lazer = await _built(None, total_score=987654, build_id=8123)
    assert lazer["total_score"] == 987654 and lazer["score_client"] == "lazer"
