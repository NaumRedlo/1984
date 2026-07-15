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


def test_long_title_truncates_before_the_mapper_block():
    """2026-07-08 redesign: title/artist truncation must respect whichever is
    tighter — the status pill margin or the mapper ("mapped by <creator>")
    block's left edge — not just the old fixed status-pill-only margin."""
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
    # _fit_pool truncates from the end, so the drawn text is a shortened
    # prefix of the full title with "…" appended — never the full string.
    title_draws = [t for t in draw_calls if t.startswith("An ") and t != full_title]
    assert title_draws and title_draws[0].endswith("…")


def test_renders_in_russian():
    png = _render(_sample(lang="ru"))
    assert png.startswith(b"\x89PNG")


def test_header_string_present_for_both_languages():
    from services.image.render.map_card import _WHATIF_STRINGS
    assert _WHATIF_STRINGS["en"]["header"] == "MAP INFORMATION"
    assert _WHATIF_STRINGS["ru"]["header"] == "ИНФОРМАЦИЯ О КАРТЕ"
    assert "mods" not in _WHATIF_STRINGS["en"]  # panel removed, key retired


def test_renders_with_mods_and_without():
    """2026-07-08 follow-up: mods moved next to the "MAP DIFFICULTY" label
    (badges, or "NM" text when none) instead of their own removed panel."""
    for mods in ("", "DT", "HDHRDT"):
        png = _render(_sample(mods=mods))
        assert png.startswith(b"\x89PNG")


def test_uses_the_shared_palette_for_accents():
    """2026-07-08: the card's own accent colours (header, mapper avatar ring,
    active-bracket highlight, pp value) were migrated to the shared
    services/image/colors module instead of local one-off constants."""
    from services.image import colors
    from services.image.render.map_card import MapCardMixin
    import inspect
    src = inspect.getsource(MapCardMixin)
    assert "colors.ACCENT" in src
    assert colors.ACCENT_PP != colors.ACCENT  # distinct pp-value tint, not reused as-is


def test_uses_the_shared_palette_for_the_base_theme():
    """2026-07-15 follow-up: the accent migration above left the card's own
    BASE palette (card/panel backgrounds, primary/muted text) on its old
    ad-hoc cool-blue constants, so it still visually didn't match the rest
    of the bot's cards. _PANEL/_WHITE/_WHATIF_CELL/_WHATIF_MUTED now all
    reference services/image/colors instead of local literal RGB tuples."""
    from services.image import colors
    from services.image.render import map_card as mc
    assert mc._PANEL == colors.CARD
    assert mc._WHITE == colors.TEXT_PRIMARY
    assert mc._WHATIF_CELL == colors.PANEL
    assert mc._WHATIF_MUTED == colors.TEXT_MUTED
    # Dead constants retired alongside the migration, not left as unused cruft.
    assert not hasattr(mc, "_WHATIF_CELL_DARK")
    assert not hasattr(mc, "_STRIP")


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


def test_renders_regardless_of_mods_combination():
    """Smoke-test: nomod, single mods, and combos all render without crashing.
    There's no mods panel on the card (removed 2026-07-08) — mods only affect
    the PP numbers computed upstream, not anything drawn here."""
    for mods in ("", "DT", "NF", "HDDT", "HRNF", "EZHDHRDTNF"):
        png = _render(_sample(mods=mods))
        assert png.startswith(b"\x89PNG")


def test_renders_gold_sr_and_mapper_avatar():
    """High SR (≥6.5) draws gold; a mapper avatar is composited when given."""
    from PIL import Image
    avatar = Image.new("RGB", (64, 64), (200, 120, 60))
    png = CardRenderer().generate_whatif_card(
        _sample(star_rating=8.6, mapper_id=123), None, [0.5] * 64, avatar)
    assert png.getvalue().startswith(b"\x89PNG")


def test_active_bracket_priority_holds_then_hands_off_at_half_percent():
    """A column holds the custom value as accuracy drops, handing off to the
    milestone below only within 0.5% of it (100% owns down to 99.6, then 99%
    takes over at 99.5)."""
    from services.image.render.map_card import MapCardMixin as M
    ms = [95.0, 98.0, 99.0, 100.0]
    assert M._whatif_active_bracket(100.0, ms) == 100.0
    assert M._whatif_active_bracket(99.6, ms) == 100.0
    assert M._whatif_active_bracket(99.5, ms) == 99.0
    assert M._whatif_active_bracket(98.6, ms) == 99.0
    assert M._whatif_active_bracket(98.5, ms) == 98.0
    assert M._whatif_active_bracket(95.5, ms) == 95.0
    assert M._whatif_active_bracket(90.0, ms) == 95.0


# ── 2026-07-15 follow-up: no "NM" label, no outline box in the PP column ────

def test_no_mods_draws_nothing_next_to_the_difficulty_label():
    """The map's own info is nomod by definition — an "NM" label there was
    just noise and was dropped. Nothing mod-related should render when
    mods_str is empty (no badges, no "NM" text)."""
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
    """2026-07-15: the accuracy pill's outline was dropped — the active row
    is now just coral text for both accuracy and pp, no box around either.
    _aa_rounded_outline is used elsewhere on the card for legitimate filled
    panels (it's what _aa_rounded_fill delegates to, with outline=None), so
    only flag a call that actually draws a visible ACCENT-colored outline
    (which is what the removed accuracy pill looked like)."""
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


# ── 2026-07-15: cover bleeds across the whole header panel ──────────────────

def test_cover_bleeds_the_full_panel_width_muted_left_vivid_right():
    from PIL import Image
    from services.image.core import CardRenderer as CR
    cover = Image.new("RGB", (400, 200), (200, 100, 50))
    bled = CR()._cover_bleed(cover, 300, 100)
    alpha = bled.getchannel("A")
    left = alpha.getpixel((2, 50))
    right = alpha.getpixel((297, 50))
    assert left > 0          # extends across the whole panel, not just the right half
    assert right > left      # right stays more vivid than the muted left


def test_cover_bleed_respects_a_custom_corner_mask():
    """profile.py's hero passes its own corner_mask (fully rounded, unlike
    the default radius-only rect) — confirms the override actually clips
    the ramp rather than being ignored."""
    from PIL import Image
    from services.image.core import CardRenderer as CR
    cover = Image.new("RGB", (300, 100), (200, 100, 50))
    mask = Image.new("L", (300, 100), 0)
    mask.paste(255, (10, 10, 290, 90))  # a small inset rect, well away from the edges
    bled = CR()._cover_bleed(cover, 300, 100, corner_mask=mask)
    alpha = bled.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0        # outside the mask: fully clipped
    assert alpha.getpixel((150, 50)) > 0      # inside the mask: visible
