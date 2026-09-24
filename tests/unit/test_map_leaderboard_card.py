from io import BytesIO

from PIL import Image

from scripts.render_lbm_demo import build
from services.image.core import CardRenderer

async def test_the_map_leaderboard_card_is_drawn_for_a_ranked_and_a_loved_map():
    renderer = CardRenderer()
    for status in ("ranked", "loved"):
        data = await build(status)
        png = (await renderer.generate_map_leaderboard_v2_async(data)).getvalue()
        img = Image.open(BytesIO(png))
        assert img.width > 800 and img.height > 600

async def test_a_loved_map_keeps_its_estimates_in_the_rows_and_the_history():
    data = await build("loved")
    assert all(row["pp_estimated"] for row in data["rows"])
    assert data["history"] and all(entry["pp_estimated"] and entry["pp"] > 0 for entry in data["history"])

async def test_a_ranked_map_shows_osu_s_own_pp():
    data = await build("ranked")
    assert not any(row["pp_estimated"] for row in data["rows"])
    assert data["rows"][0]["pp"] == 720.0

def test_when_a_record_was_set_reads_like_speech():
    from datetime import datetime, timezone

    from services.image.render.map_leaderboard import _when

    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    assert _when(datetime(2026, 9, 24, 9, tzinfo=timezone.utc), "ru", now) == "сегодня"
    assert _when(datetime(2026, 9, 23, 9, tzinfo=timezone.utc), "en", now) == "yesterday"
    assert _when(datetime(2026, 9, 21, 9, tzinfo=timezone.utc), "ru", now) == "3 дня назад"
    assert _when(datetime(2026, 9, 19, 9, tzinfo=timezone.utc), "ru", now) == "5 дней назад"
    assert _when(datetime(2026, 9, 10, 9, tzinfo=timezone.utc), "ru", now) == "10 сен"
    assert _when(datetime(2026, 9, 10, 9, tzinfo=timezone.utc), "en", now) == "Sep 10"
    assert _when(datetime(2025, 3, 2, 9, tzinfo=timezone.utc), "ru", now) == "2 мар 2025"
    assert _when(None, "ru", now) == ""

async def test_a_reader_with_no_result_and_one_off_the_page_both_get_their_own_panel():
    data = await build("ranked")
    data["viewer"] = {"username": "newbie"}
    png = (await CardRenderer().generate_map_leaderboard_v2_async(data)).getvalue()
    with_panel = Image.open(BytesIO(png)).height
    data["viewer"] = data["rows"][0]
    without = Image.open(BytesIO((await CardRenderer().generate_map_leaderboard_v2_async(data)).getvalue())).height
    assert with_panel > without
