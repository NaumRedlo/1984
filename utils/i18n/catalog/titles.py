"""titles message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
    # ── tt (titles collection) / st (set title) ──────────────────────────
    "tt.load_error": {
        "en": "An error occurred while loading the title collection.",
        "ru": "Произошла ошибка при загрузке коллекции титулов.",
    },
    "tt.not_your_collection": {"en": "Not your collection.", "ru": "Не ваша коллекция."},
    "tt.stale": {
        "en": "Expired — write titles again.",
        "ru": "Устарело — напишите titles заново.",
    },
    "st.usage": {
        "en": "Usage: <code>st &lt;name&gt;</code> or <code>st off</code>.",
        "ru": "Использование: <code>st &lt;имя&gt;</code> или <code>st off</code>.",
    },
    "st.cleared": {"en": "Title cleared.", "ru": "Титул снят."},
    "st.not_found": {
        "en": "No unlocked title matches “{query}”.",
        "ru": "Нет открытого титула по запросу «{query}».",
    },
    "st.ambiguous": {
        "en": "Ambiguous — several match: {names}.",
        "ru": "Уточни — подходит несколько: {names}.",
    },
    "st.set": {
        "en": "Active title: <b>{name}</b> ({rarity}). Shown in cards.",
        "ru": "Активный титул: <b>{name}</b> ({rarity}). Виден в карточках.",
    },
}
