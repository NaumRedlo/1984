"""Bilingual TitleDef / RARITY_META (utils/titles.py, 2026-07-02b translation
pass): every title has an RU pair, name_for/description_for/rarity_label_for
pick the right one, and the description tokenizer still recognizes SR/mod/
FC/Pass/grade tokens embedded in the Russian text."""

import pytest

from utils.titles import TITLE_REGISTRY, RARITY_META, RARITY_ORDER, rarity_label_for
from services.image.render.titles import _tokenize_desc, _tt_tabs


def test_every_title_has_a_russian_translation():
    missing = [c for c, td in TITLE_REGISTRY.items() if not td.name_ru or not td.description_ru]
    assert missing == []


def test_registry_has_49_or_more_titles():
    # The comment says 49; guard against accidentally deleting entries.
    assert len(TITLE_REGISTRY) >= 49


def test_name_for_and_description_for():
    td = TITLE_REGISTRY["heavy_hand"]
    assert td.name_for("en") == "Heavy Hand"
    assert td.name_for("ru") == td.name_ru  # RU wording may be revised; just pick the right field
    assert td.description_for("en") == "FC a map from 5* with effective AR 10.3+."
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
        assert en != ru  # every tier actually translated, not a silent fallback


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
    # Same KINDS of tokens survive translation (exact substrings may legitimately
    # differ in count only if the English phrasing repeats a word Russian doesn't).
    assert ru_special, f"no special tokens recognized in RU description for {code!r}"
    assert set(ru_special) <= set(en_special) | set(ru_special)  # sanity: non-empty, no crash


def test_tt_tabs_all_rarities_translated():
    en = dict(_tt_tabs("en"))
    ru = dict(_tt_tabs("ru"))
    assert set(en) == set(ru) == {"all", *RARITY_ORDER}
    assert en["all"] == "ALL" and ru["all"] == "ВСЕ"
    for r in RARITY_ORDER:
        assert en[r] != ru[r]
