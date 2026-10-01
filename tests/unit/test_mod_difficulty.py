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

    assert MOD_DIFFICULTY["DT"] == MOD_DIFFICULTY["NC"] == 1.10
    assert MOD_DIFFICULTY["HT"] == 0.30

def test_mods_are_read_however_they_were_written():
    from utils.osu.mod_utils import mod_tokens

    assert mod_tokens("HD,DT") == ("HD", "DT")
    assert mod_tokens("+HD,HR,DT") == ("HD", "HR", "DT")
    assert mod_tokens("HDDTHR") == ("HD", "DT", "HR")
    assert mod_tokens(["HD", {"acronym": "dt"}]) == ("HD", "DT")
    assert mod_tokens("—") == () and mod_tokens(None) == ()
    assert mod_difficulty("HD,HR") == mod_difficulty("HDHR")
    assert mod_difficulty("HD,DT") > mod_difficulty("FL")

def test_the_stars_a_play_was_set_on_say_how_hard_its_mods_were():
    from utils.osu.mod_utils import mods_hardness

    assert abs(mods_hardness("DT", 9.8, 7.0) - 1.4) < 1e-9
    assert mods_hardness("HD,DT", 9.8, 7.0) > mods_hardness("DT", 9.8, 7.0)
    assert mods_hardness("HR", 7.1, 7.0) < mods_hardness("DT", 9.8, 7.0)
    assert mods_hardness("EZ", 5.5, 7.0) < 1.0 < mods_hardness("HD", 7.0, 7.0)
    assert mods_hardness("CL,HD,HR", 7.4, 7.0) == mods_hardness("HD,HR", 7.4, 7.0)
    assert mods_hardness("", 7.0, 7.0) == 1.0 and mods_hardness("CL", None, None) == 1.0

def test_stars_that_did_not_move_under_a_mod_that_moves_them_are_not_believed():
    from utils.osu.mod_utils import mods_hardness

    assert mods_hardness("DT", 7.0, 7.0) == mod_difficulty("DT")
    assert mods_hardness("HD,DT", None, 7.0) == mod_difficulty("HDDT")
    assert mods_hardness("DT,NF", 9.8, 7.0) < 1.0
