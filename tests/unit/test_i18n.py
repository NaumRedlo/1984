"""The central translation catalog + t() helper (utils/i18n.py)."""

import re
import string

import pytest

from utils.i18n import DEFAULT_LANG, SUPPORTED_LANGS, t
from utils.i18n import _CATALOG


def test_returns_requested_language():
    assert t("common.api_not_ready", "ru") == "Ошибка: API-клиент не инициализирован."
    assert t("common.api_not_ready", "en") == "Error: API client is not initialised."


def test_falls_back_to_default_language():
    # A lang with no entry falls back to DEFAULT_LANG (en), never crashes.
    assert t("common.api_not_ready", "de") == t("common.api_not_ready", DEFAULT_LANG)


def test_unknown_key_returns_the_key():
    assert t("does.not.exist", "en") == "does.not.exist"


def test_formats_placeholders():
    out = t("common.user_not_found", "en", name="cookiezi")
    assert "cookiezi" in out and "{name}" not in out


def test_lang_is_case_insensitive():
    assert t("cmp.same_player", "RU") == t("cmp.same_player", "ru")


def _placeholders(template: str) -> set:
    # field names used by str.format ({name} -> "name"); ignore bare {}/positional
    return {fname for _, fname, _, _ in string.Formatter().parse(template) if fname}


@pytest.mark.parametrize("key", sorted(_CATALOG))
def test_every_key_has_all_supported_langs(key):
    entry = _CATALOG[key]
    for lang in SUPPORTED_LANGS:
        assert lang in entry, f"{key} missing {lang}"


@pytest.mark.parametrize("key", sorted(_CATALOG))
def test_placeholders_match_across_languages(key):
    entry = _CATALOG[key]
    ref = _placeholders(entry[DEFAULT_LANG])
    for lang, template in entry.items():
        assert _placeholders(template) == ref, f"{key}[{lang}] placeholders differ from {DEFAULT_LANG}"


def test_no_stray_cyrillic_in_english_strings():
    for key, entry in _CATALOG.items():
        assert not re.search(r"[А-Яа-яЁё]", entry["en"]), f"{key}[en] contains Cyrillic"
