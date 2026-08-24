from utils.osu.mod_utils import MOD_DIFFICULTY, mod_difficulty


def test_no_mod_is_the_middle_of_the_scale():
    assert mod_difficulty("") == 1.0
    assert mod_difficulty(None) == 1.0
    assert mod_difficulty("HD") > 1.0
    assert mod_difficulty("NF") < 1.0


def test_a_combination_outranks_the_mods_it_is_made_of():
    assert mod_difficulty("HDHR") > mod_difficulty("HD")
    assert mod_difficulty("HDHR") > mod_difficulty("HR")
    assert mod_difficulty("HDHRDT") > mod_difficulty("HDHR")


def test_the_order_reads_the_way_a_player_would_say_it():
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
    assert mod_difficulty("RX") < mod_difficulty("HT") < mod_difficulty("EZ")


def test_an_unknown_acronym_counts_for_nothing():
    assert mod_difficulty("XX") == 1.0
    assert mod_difficulty("HDXX") == mod_difficulty("HD")


def test_the_scale_stays_the_engine_s():
    assert MOD_DIFFICULTY["HD"] == MOD_DIFFICULTY["HR"] == 1.06
    assert MOD_DIFFICULTY["FL"] == 1.12
    # `rate_adjust_v1` at the standard rates: 1.5x and 0.75x.
    assert MOD_DIFFICULTY["DT"] == MOD_DIFFICULTY["NC"] == 1.10
    assert MOD_DIFFICULTY["HT"] == 0.30
