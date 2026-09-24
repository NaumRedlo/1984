from __future__ import annotations

from typing import Dict

from utils.i18n.catalog import (
    common, account, settings, leaderboard, titles, profile, whatif, misc, dossier, players,
)

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "ru")

_CATALOG_MODULES = (
    common, account, settings, leaderboard, titles, profile, whatif, misc, dossier, players,
)

_CATALOG: Dict[str, Dict[str, str]] = {}
for _module in _CATALOG_MODULES:
    _CATALOG.update(_module.CATALOG)

def t(key: str, lang: str = DEFAULT_LANG, /, **kwargs) -> str:
    entry = _CATALOG.get(key)
    if entry is None:
        return key
    lang = (lang or DEFAULT_LANG).lower()
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or next(iter(entry.values()))
    return text.format(**kwargs) if kwargs else text

__all__ = ["t", "DEFAULT_LANG", "SUPPORTED_LANGS"]
