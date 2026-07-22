"""Central translation catalog for the bot's Telegram message/button text.

Card-drawn text keeps its own per-renderer string dicts (services/image/...);
this module is for everything sent as a Telegram message, caption, button
label or callback answer.

Usage:
    from utils.i18n import t
    lang = (await get_language(user_id)).lower()
    await message.answer(t("cmp.usage", lang), parse_mode="HTML")
    await message.answer(t("common.user_not_found", lang, name=escape_html(q)))

Keys live under dotted namespaces ("common.*" for strings shared across
handlers, "<area>.*" for area-specific ones). Placeholders use str.format
style ({name}); pass them as keyword args. A missing key returns the key
itself so it's obvious in-chat rather than crashing.

The strings themselves live in per-area modules under ``catalog/`` and are
merged into ``_CATALOG`` below; add a new area by dropping a module there and
listing it in ``_CATALOG_MODULES``.

Admin/owner-only text and dev logs are intentionally NOT localised — they
stay in Russian in their own handlers.
"""

from __future__ import annotations

from typing import Dict

from utils.i18n.catalog import (
    common, account, render, settings, leaderboard, titles, profile, whatif, misc,
    requests,
)

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "ru")

# Merge the per-area slices into one lookup table. Areas carry disjoint key
# namespaces ("<area>.*"), so update order doesn't matter.
_CATALOG_MODULES = (
    common, account, render, settings, leaderboard, titles, profile, whatif, misc,
    requests,
)

# key -> {lang -> template}
_CATALOG: Dict[str, Dict[str, str]] = {}
for _module in _CATALOG_MODULES:
    _CATALOG.update(_module.CATALOG)


def t(key: str, lang: str = DEFAULT_LANG, /, **kwargs) -> str:
    """Translate `key` into `lang`, formatting any `{placeholder}` with kwargs.

    Falls back to the default language if the key has no entry for `lang`,
    and to the key itself if the key is unknown (so a missing string shows up
    in-chat instead of raising)."""
    entry = _CATALOG.get(key)
    if entry is None:
        return key
    lang = (lang or DEFAULT_LANG).lower()
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or next(iter(entry.values()))
    return text.format(**kwargs) if kwargs else text


__all__ = ["t", "DEFAULT_LANG", "SUPPORTED_LANGS"]
