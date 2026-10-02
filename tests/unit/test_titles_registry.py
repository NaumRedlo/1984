import pytest

from utils.titles import TITLE_REGISTRY, RARITY_META, RARITY_ORDER, rarity_label_for
from services.image.render.titles import _tokenize_desc, _tt_tabs

def test_every_title_has_a_russian_translation():
    missing = [c for c, td in TITLE_REGISTRY.items() if not td.name_ru or not td.description_ru]
    assert missing == []

def test_registry_has_49_or_more_titles():

    assert len(TITLE_REGISTRY) >= 49

def test_name_for_and_description_for():

    td = TITLE_REGISTRY["heavy_hand"]
    assert td.name_for("en") == td.name
    assert td.name_for("ru") == td.name_ru
    assert td.description_for("en") == td.description
    assert "FC" in td.description_for("en") and "5*" in td.description_for("en")
    assert "FC" in td.description_for("ru") and "5*" in td.description_for("ru")

def test_name_for_defaults_to_english_for_unknown_lang():
    td = TITLE_REGISTRY["registered"]
    assert td.name_for("fr") == td.name
    assert td.name_for(None) == td.name

def test_rarity_label_for_every_tier():
    for rarity in RARITY_ORDER:
        en = rarity_label_for(rarity, "en")
        ru = rarity_label_for(rarity, "ru")
        assert en == RARITY_META[rarity]["label"]
        assert ru == RARITY_META[rarity]["label_ru"]
        assert en != ru

def test_title_rarity_label_for_method_matches_module_function():
    td = TITLE_REGISTRY["ss_100"]
    assert td.rarity_label_for("ru") == rarity_label_for(td.rarity, "ru")

@pytest.mark.parametrize("code", [
    "rank_d", "reeducated", "ss_100", "heavy_hand", "sr_10", "doublethink", "perfectionist",
])
def test_description_tokenizer_recognizes_tokens_in_russian_text(code):
    td = TITLE_REGISTRY[code]
    en_special = [k for _, k in _tokenize_desc(td.description) if k != "text"]
    ru_special = [k for _, k in _tokenize_desc(td.description_ru) if k != "text"]

    assert ru_special, f"no special tokens recognized in RU description for {code!r}"
    assert set(ru_special) <= set(en_special) | set(ru_special)

def test_tt_tabs_all_rarities_translated():
    en = dict(_tt_tabs("en"))
    ru = dict(_tt_tabs("ru"))
    assert set(en) == set(ru) == {"all", *RARITY_ORDER}
    assert en["all"] == "ALL" and ru["all"] == "ВСЕ"
    for r in RARITY_ORDER:
        assert en[r] != ru[r]

def test_there_is_no_secret_tier_and_the_titles_that_were_secret_are_anomalies_that_stay_hidden():
    assert "secret" not in RARITY_ORDER and "secret" not in RARITY_META
    assert RARITY_ORDER[-1] == "anomaly"
    hidden = {code for code, td in TITLE_REGISTRY.items() if td.secret}
    assert hidden == {"doublethink", "repeat_15", "compare_50", "comeback_180d", "magic7", "choke_95"}
    for code in hidden:
        td = TITLE_REGISTRY[code]
        assert td.rarity == "anomaly" and td.hint and td.hint_ru

def test_the_catalogue_tells_which_titles_are_hidden_until_earned():
    from services.render_farm import community

    catalogue = {t["code"]: t for t in community.titles_catalogue()}
    assert catalogue["magic7"]["hidden"] is True and catalogue["magic7"]["rarity"] == "anomaly"
    assert catalogue["wysi"]["hidden"] is False
    assert catalogue["dejavu"]["rarity"] == "anomaly" and catalogue["dejavu"]["hidden"] is False

def test_the_rebalanced_tiers_are_the_ones_the_plan_asked_for():
    tier = {code: td.rarity for code, td in TITLE_REGISTRY.items()}
    assert (tier["s_50"], tier["ss_100"], tier["archaeologist"], tier["heavy_hand"]) == ("uncommon", "rare", "uncommon", "epic")
    assert (tier["ss_hdfl_5"], tier["sr_10"], tier["archivist"], tier["streak_30d"]) == ("legendary", "legendary", "epic", "epic")
    assert tier["dejavu"] == "anomaly" and tier["off_day"] == "rare" and tier["ss_streak_10"] == "mythic"
    assert TITLE_REGISTRY["week_500"].target == 300 and TITLE_REGISTRY["ss_streak_10"].target == 5
    assert TITLE_REGISTRY["session_30maps"].target == 50 and TITLE_REGISTRY["session_3h"].target == 180

def test_a_description_fits_beside_the_tier_pill_in_both_languages():
    for code, td in TITLE_REGISTRY.items():
        assert len(td.description) <= 70 and len(td.description_ru) <= 70, code

def test_no_mod_is_drawn_as_a_mod_badge_like_the_others():
    from services.image.utils import load_mod_icon

    tokens = _tokenize_desc("Pass maps from 5* with NM, HD, HR, DT and FL.")
    assert [seg for seg, kind in tokens if kind == "mod"] == ["NM", "HD", "HR", "DT", "FL"]
    ru = _tokenize_desc(TITLE_REGISTRY["four_ministries"].description_ru)
    assert "NM" in [seg for seg, kind in ru if kind == "mod"]
    icon = load_mod_icon("NM", size=27)
    assert icon is not None and icon.getbbox() is not None
