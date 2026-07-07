"""services/image/render/recent.py's build_recent_card_data — the field-
mapping/PP-calculation logic extracted out of bot/handlers/profile/recent.py's
cmd_recent, now shared with the score-link auto-detect handler."""

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
    # Patch the import SITE (services.image.render.recent.calculate_pp), not
    # utils.osu.pp_calculator.calculate_pp directly — recent.py imported the
    # name into its own namespace, so patching the origin module wouldn't
    # affect what build_recent_card_data actually calls.
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
    assert data["star_rating"] == 7.9  # from the (faked) mod-adjusted PP calc
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
    """Bridges build_recent_card_data -> generate_recent_card as a sanity
    check that the two pieces actually agree on the data-dict shape."""
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
    # Falls back to the score's own (nominal) star rating and API-provided pp.
    assert data["star_rating"] == 7.42
    assert data["pp"] == 300.0
    assert data["pp_if_fc"] == 0.0 and data["pp_if_ss"] == 0.0
