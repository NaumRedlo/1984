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
