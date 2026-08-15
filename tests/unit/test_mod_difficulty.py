"""Ordering mod combinations by how hard they make a play.

What the map leaderboard's "hardest mods" line is picked with. The scale is not
this file's invention and must not become one: it mirrors the multipliers the
engine already implements for stable's ScoreV1, in
`dossier/crates/dossier-sim/src/multiplier.rs`.
"""

from utils.osu.mod_utils import MOD_DIFFICULTY, mod_difficulty


def test_no_mod_is_the_middle_of_the_scale():
    """A bare play is 1.0, and everything else is read against it: mods the
    game rewards more for sit above, the ones it rewards less for below."""
    assert mod_difficulty("") == 1.0
    assert mod_difficulty(None) == 1.0
    assert mod_difficulty("HD") > 1.0
    assert mod_difficulty("NF") < 1.0


def test_a_combination_outranks_the_mods_it_is_made_of():
    """The property the whole feature leans on. On a board where most plays
    carry two or three mods, a scale that ranked HDHR level with HD would have
    nothing to say."""
    assert mod_difficulty("HDHR") > mod_difficulty("HD")
    assert mod_difficulty("HDHR") > mod_difficulty("HR")
    assert mod_difficulty("HDHRDT") > mod_difficulty("HDHR")


def test_the_order_reads_the_way_a_player_would_say_it():
    """Not an exhaustive pin — the numbers are the engine's and may move with
    it — but the shape has to hold: the speed and vision mods above a bare
    play, the difficulty reductions below it, and the assists at the bottom."""
    order = sorted(
        ["", "HD", "HR", "DT", "FL", "HDHR", "EZ", "HT", "NF", "RX", "SO"],
        key=mod_difficulty,
        reverse=True,
    )
    assert order.index("HDHR") < order.index("HD")
    assert order.index("DT") < order.index("")
    assert order.index("") < order.index("NF")
    assert order[-1] == "RX", f"an assist should rank last, got {order}"


def test_relax_ranks_below_every_difficulty_reduction():
    """Relax plays the map for you, which is a different thing from making it
    easier — it belongs under EZ and HT rather than beside them."""
    assert mod_difficulty("RX") < mod_difficulty("HT") < mod_difficulty("EZ")


def test_an_unknown_acronym_counts_for_nothing():
    """A mod nothing here knows must not quietly drag a play to either end of
    the order — the lazer set grows, and this file will always be behind it."""
    assert mod_difficulty("XX") == 1.0
    assert mod_difficulty("HDXX") == mod_difficulty("HD")


def test_the_scale_stays_the_engine_s():
    """A guard against someone tuning these by feel. If the engine's stable
    multipliers change, this file is meant to be updated from it, not drift."""
    assert MOD_DIFFICULTY["HD"] == MOD_DIFFICULTY["HR"] == 1.06
    assert MOD_DIFFICULTY["FL"] == 1.12
    # `rate_adjust_v1` at the standard rates: 1.5x and 0.75x.
    assert MOD_DIFFICULTY["DT"] == MOD_DIFFICULTY["NC"] == 1.10
    assert MOD_DIFFICULTY["HT"] == 0.30
