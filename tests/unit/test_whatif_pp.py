from utils.osu.mod_utils import KNOWN_PP_MODS, MOD_BITS, parse_mods_tokens

def test_parse_mods_tokens_splits_pairs():
    assert parse_mods_tokens("HDDT") == ("HD", "DT")
    assert parse_mods_tokens("") == ()
    assert parse_mods_tokens("HR") == ("HR",)

def test_known_pp_mods_matches_mod_bits_keys():
    assert KNOWN_PP_MODS == frozenset(MOD_BITS)
    assert "HR" in KNOWN_PP_MODS and "XY" not in KNOWN_PP_MODS
