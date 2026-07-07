"""Headless render checks for the `map` command's what-if card
(services/image/render/map_card.py's generate_whatif_card): thumbnail
header, SR/BPM/length/combo + CS/AR/OD/HP grids, the reused strain graph,
mod highlighting, and the PP-by-accuracy bracket table. Also a regression
guard that the sibling generate_map_card still renders after the shared
_draw_identity_header extraction."""

from services.image.core import CardRenderer


def _sample(**overrides):
    data = {
        "beatmap_id": 129891, "beatmapset_id": 39804,
        "artist": "xi", "title": "FREEDOM DiVE", "version": "FOUR DIMENSIONS",
        "creator": "Nakagawa-Kanon", "status": "ranked", "cover_url": None,
        "url": "https://osu.ppy.sh/beatmapsets/39804#osu/129891",
        "star_rating": 7.42, "accuracy": 94.0, "mods": "HR", "pp": 227,
        "max_combo": 720, "count_300": 550, "count_100": 8, "count_50": 0, "count_miss": 0,
        "cs": 4.4, "ar": 10.3, "od": 9.7, "hp_drain": 8.0, "bpm": 180, "length": 126,
        "brackets": {95.0: 190.0, 98.0: 240.0, 99.0: 265.0, 100.0: 300.0},
    }
    data.update(overrides)
    return data


def _render(data, strains=None):
    return CardRenderer().generate_whatif_card(data, None, strains).getvalue()


def test_renders_nomod():
    png = _render(_sample(mods="", accuracy=100.0))
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_hr_dt():
    png = _render(_sample(mods="HRDT"))
    assert png.startswith(b"\x89PNG")


def test_renders_with_strain_data():
    png = _render(_sample(), strains=[i / 63 for i in range(64)])
    assert png.startswith(b"\x89PNG")


def test_renders_without_strain_data():
    """No strains (e.g. calculate_strains failed) -> the graph panel shows
    NO DATA but the card still renders."""
    png = _render(_sample(), strains=None)
    assert png.startswith(b"\x89PNG")


def test_renders_long_title_without_crash():
    png = _render(_sample(
        title="An Extremely Long Beatmap Title That Could Break The Layout (TV Size) [Extra Difficulty]",
        artist="A Very Long Artist Name That Might Overflow The Row",
    ))
    assert png.startswith(b"\x89PNG")


def test_renders_zero_counts():
    png = _render(_sample(count_300=0, count_100=0, count_50=0, count_miss=0, max_combo=0))
    assert png.startswith(b"\x89PNG")


def test_renders_with_empty_brackets():
    """Defensive: an empty/missing brackets dict must not crash the card
    (division-by-zero guard in _whatif_pp_brackets)."""
    png = _render(_sample(brackets={}))
    assert png.startswith(b"\x89PNG")


def test_map_card_still_renders_after_identity_header_extraction():
    """Regression guard: refactoring generate_map_card's header block into
    the shared _draw_identity_header must not change its own behaviour."""
    data = _sample()
    buf = CardRenderer().generate_map_card(data, None)
    png = buf.getvalue()
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_whatif_mods_row_handles_every_dt_nc_combination():
    """DT has no pill slot of its own — it lights up NC's (both are the
    speed-up bucket). Smoke-tests every combination doesn't crash."""
    for mods in ("", "DT", "NC", "HDDT", "HRNC"):
        png = _render(_sample(mods=mods))
        assert png.startswith(b"\x89PNG")
