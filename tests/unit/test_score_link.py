"""Parser for osu! score links pasted in chat (auto score-card feature)."""

import pytest

from utils.osu.score_link import extract_score_ref, LINK_HINT_RE


def test_modern_link():
    r = extract_score_ref("check https://osu.ppy.sh/scores/123456789 nice")
    assert (r.score_id, r.mode) == (123456789, None)


@pytest.mark.parametrize("mode", ["osu", "taiko", "fruits", "mania"])
def test_legacy_link_all_modes(mode):
    r = extract_score_ref(f"https://osu.ppy.sh/scores/{mode}/987654321")
    assert (r.score_id, r.mode) == (987654321, mode)


def test_scheme_optional():
    r = extract_score_ref("osu.ppy.sh/scores/555")
    assert (r.score_id, r.mode) == (555, None)


def test_case_insensitive():
    r = extract_score_ref("OSU.PPY.SH/scores/OSU/42")
    assert (r.score_id, r.mode) == (42, "osu")


@pytest.mark.parametrize("text", [
    "", "no links here", "random 12345 number",
    "https://example.com/scores/1", "ppy.sh but no path",
    "https://osu.ppy.sh/beatmaps/123",  # a beatmap link, not a score link
])
def test_no_match(text):
    assert extract_score_ref(text) is None


def test_link_hint_matches_only_score_links():
    assert LINK_HINT_RE.search("x osu.ppy.sh/scores/1 y")
    assert LINK_HINT_RE.search("x osu.ppy.sh/scores/osu/1 y")
    assert not LINK_HINT_RE.search("osu.ppy.sh/beatmaps/1")
    assert not LINK_HINT_RE.search("just talking about osu and ppy")


def test_no_overlap_with_beatmap_link_hint():
    """The two auto-detect handlers (maplink/scorelink) must never both fire
    on the same message — their LINK_HINT_RE patterns must stay disjoint."""
    from utils.osu.beatmap_link import LINK_HINT_RE as MAP_HINT

    assert not MAP_HINT.search("osu.ppy.sh/scores/123")
    assert not LINK_HINT_RE.search("osu.ppy.sh/beatmapsets/1#osu/2")
