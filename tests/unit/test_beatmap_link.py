"""Parser for osu! beatmap links pasted in chat (auto map-card feature)."""

import pytest

from utils.osu.beatmap_link import extract_beatmap_ref, LINK_HINT_RE


def test_full_set_diff_link():
    r = extract_beatmap_ref("see https://osu.ppy.sh/beatmapsets/39804#osu/252238 !")
    assert (r.beatmap_id, r.beatmapset_id, r.mode) == (252238, 39804, "osu")


def test_set_only_link():
    r = extract_beatmap_ref("https://osu.ppy.sh/beatmapsets/39804")
    assert (r.beatmap_id, r.beatmapset_id) == (None, 39804)


def test_mode_anchor_kept():
    r = extract_beatmap_ref("https://osu.ppy.sh/beatmapsets/39804#mania/999")
    assert (r.beatmap_id, r.beatmapset_id, r.mode) == (999, 39804, "mania")


@pytest.mark.parametrize("text,bid", [
    ("osu.ppy.sh/beatmaps/252238", 252238),     # scheme optional
    ("https://osu.ppy.sh/b/252238", 252238),    # legacy /b/
])
def test_single_beatmap_links(text, bid):
    r = extract_beatmap_ref(text)
    assert r.beatmap_id == bid and r.beatmapset_id is None


def test_legacy_set_link():
    r = extract_beatmap_ref("https://osu.ppy.sh/s/39804")
    assert r.beatmapset_id == 39804 and r.beatmap_id is None


@pytest.mark.parametrize("text", [
    "", "no links here", "random 12345 number",
    "https://example.com/beatmaps/1", "ppy.sh but no path",
])
def test_no_match(text):
    assert extract_beatmap_ref(text) is None


def test_link_hint_matches_only_real_links():
    assert LINK_HINT_RE.search("x osu.ppy.sh/beatmapsets/1 y")
    assert not LINK_HINT_RE.search("just talking about osu and ppy")
