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
    png = _render(_sample(), strains=None)
    assert png.startswith(b"\x89PNG")

def test_renders_long_title_without_crash():
    png = _render(_sample(
        title="An Extremely Long Beatmap Title That Could Break The Layout (TV Size) [Extra Difficulty]",
        artist="A Very Long Artist Name That Might Overflow The Row",
    ))
    assert png.startswith(b"\x89PNG")

def test_long_title_truncates_before_the_mapper_block():
    from services.image.core import CardRenderer
    renderer = CardRenderer()
    draw_calls = []
    original = renderer._draw_text

    def spy(draw, pos, text, font, color, **kwargs):
        draw_calls.append(text)
        return original(draw, pos, text, font, color, **kwargs)

    renderer._draw_text = spy
    full_title = "An Extremely Long Beatmap Title That Could Break The Layout"
    renderer.generate_whatif_card(_sample(
        title=full_title, creator="AVeryLongMapperUsernameThatTakesUpSpace",
    ))

    title_draws = [t for t in draw_calls if t.startswith("An ") and t != full_title]
    assert title_draws and title_draws[0].endswith("…")

def test_renders_in_russian():
    png = _render(_sample(lang="ru"))
    assert png.startswith(b"\x89PNG")

def test_header_string_present_for_both_languages():
    from services.image.render.map_card import _WHATIF_STRINGS
    assert _WHATIF_STRINGS["en"]["header"] == "MAP INFORMATION"
    assert _WHATIF_STRINGS["ru"]["header"] == "ИНФОРМАЦИЯ О КАРТЕ"
    assert "mods" not in _WHATIF_STRINGS["en"]

def test_renders_with_mods_and_without():
    for mods in ("", "DT", "HDHRDT"):
        png = _render(_sample(mods=mods))
        assert png.startswith(b"\x89PNG")

def test_uses_the_shared_palette_for_accents():
    from services.image import colors
    from services.image.render.map_card import MapCardMixin
    import inspect
    src = inspect.getsource(MapCardMixin)
    assert "colors.ACCENT" in src
    assert colors.ACCENT_PP != colors.ACCENT

def test_uses_the_shared_palette_for_the_base_theme():
    from services.image import colors
    from services.image.render import map_card as mc
    assert mc._PANEL == colors.CARD
    assert mc._WHITE == colors.TEXT_PRIMARY
    assert mc._WHATIF_CELL == colors.PANEL
    assert mc._WHATIF_MUTED == colors.TEXT_MUTED

    assert not hasattr(mc, "_WHATIF_CELL_DARK")
    assert not hasattr(mc, "_STRIP")

def test_renders_zero_counts():
    png = _render(_sample(count_300=0, count_100=0, count_50=0, count_miss=0, max_combo=0))
    assert png.startswith(b"\x89PNG")

def test_renders_with_empty_brackets():
    png = _render(_sample(brackets={}))
    assert png.startswith(b"\x89PNG")

def test_map_card_still_renders_after_identity_header_extraction():
    data = _sample()
    buf = CardRenderer().generate_map_card(data, None)
    png = buf.getvalue()
    assert png.startswith(b"\x89PNG") and len(png) > 2000

def test_renders_regardless_of_mods_combination():
    for mods in ("", "DT", "NF", "HDDT", "HRNF", "EZHDHRDTNF"):
        png = _render(_sample(mods=mods))
        assert png.startswith(b"\x89PNG")

def test_renders_gold_sr_and_mapper_avatar():
    from PIL import Image
    avatar = Image.new("RGB", (64, 64), (200, 120, 60))
    png = CardRenderer().generate_whatif_card(
        _sample(star_rating=8.6, mapper_id=123), None, [0.5] * 64, avatar)
    assert png.getvalue().startswith(b"\x89PNG")

def test_active_bracket_priority_holds_then_hands_off_at_half_percent():
    from services.image.render.map_card import MapCardMixin as M
    ms = [95.0, 98.0, 99.0, 100.0]
    assert M._whatif_active_bracket(100.0, ms) == 100.0
    assert M._whatif_active_bracket(99.6, ms) == 100.0
    assert M._whatif_active_bracket(99.5, ms) == 99.0
    assert M._whatif_active_bracket(98.6, ms) == 99.0
    assert M._whatif_active_bracket(98.5, ms) == 98.0
    assert M._whatif_active_bracket(95.5, ms) == 95.0
    assert M._whatif_active_bracket(90.0, ms) == 95.0

def test_no_mods_draws_nothing_next_to_the_difficulty_label():
    from services.image.core import CardRenderer as CR
    renderer = CR()
    draw_calls = []
    original = renderer._draw_text

    def spy(draw, pos, text, font, color, **kwargs):
        draw_calls.append(text)
        return original(draw, pos, text, font, color, **kwargs)

    renderer._draw_text = spy
    renderer.generate_whatif_card(_sample(mods=""))
    assert "NM" not in draw_calls

def test_pp_column_active_row_has_no_outline_box():
    from services.image import colors
    from services.image.core import CardRenderer as CR
    renderer = CR()
    outline_calls = []
    original = renderer._aa_rounded_outline

    def spy(*args, **kwargs):
        outline_calls.append(kwargs.get("outline"))
        return original(*args, **kwargs)

    renderer._aa_rounded_outline = spy
    renderer.generate_whatif_card(_sample())
    assert colors.ACCENT not in outline_calls

def test_pp_column_active_row_uses_matching_accent_colors():
    from services.image.render.map_card import MapCardMixin as M
    import inspect
    src = inspect.getsource(M._whatif_pp_column)
    assert "colors.ACCENT_PP" in src
    assert "colors.ACCENT" in src

def test_cover_bleeds_the_full_panel_width_muted_left_vivid_right():
    from PIL import Image
    from services.image.core import CardRenderer as CR
    cover = Image.new("RGB", (400, 200), (200, 100, 50))
    bled = CR()._cover_bleed(cover, 300, 100)
    alpha = bled.getchannel("A")
    left = alpha.getpixel((2, 50))
    right = alpha.getpixel((297, 50))
    assert left > 0
    assert right > left

def test_cover_bleed_respects_a_custom_corner_mask():
    from PIL import Image
    from services.image.core import CardRenderer as CR
    cover = Image.new("RGB", (300, 100), (200, 100, 50))
    mask = Image.new("L", (300, 100), 0)
    mask.paste(255, (10, 10, 290, 90))
    bled = CR()._cover_bleed(cover, 300, 100, corner_mask=mask)
    alpha = bled.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((150, 50)) > 0
