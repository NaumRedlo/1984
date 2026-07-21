"""whatif message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
    # ── wif (map / what-if command) ──────────────────────────────────────
    "wif.kb.mods": {"en": "🎛 Mods", "ru": "🎛 Моды"},
    "wif.kb.acc": {"en": "🎯 Accuracy", "ru": "🎯 Точность"},
    "wif.usage": {
        "en": ("Reply to a beatmap card with accuracy and mods: <code>80 hr</code>\n"
               "(The card appears automatically when a beatmap link is posted in chat.)"),
        "ru": ("Ответь на карточку карты точностью и модами: <code>80 hr</code>\n"
               "(Карточка появляется автоматически, когда в чат кидают ссылку на карту.)"),
    },
    "wif.need_accuracy": {
        "en": "Specify accuracy, e.g. <code>94 hr</code>",
        "ru": "Укажи точность, например: <code>94 hr</code>",
    },
    "wif.bad_accuracy": {
        "en": "Invalid accuracy: <code>{value}</code>",
        "ru": "Некорректная точность: <code>{value}</code>",
    },
    "wif.accuracy_range": {
        "en": "Accuracy must be between 0 and 100%.",
        "ru": "Точность должна быть в диапазоне 0–100%.",
    },
    "wif.unknown_mod": {
        "en": "Unknown mod: <code>{mods}</code>",
        "ru": "Неизвестный мод: <code>{mods}</code>",
    },
    "wif.map_not_found": {
        "en": "Beatmap not found, or pp couldn't be calculated.",
        "ru": "Карта не найдена или не удалось рассчитать pp.",
    },
    "wif.render_failed": {
        "en": "Couldn't render the card.",
        "ru": "Не удалось отрисовать карточку.",
    },
    "wif.recalc_failed": {
        "en": "Couldn't recalculate.",
        "ru": "Не удалось пересчитать.",
    },
}
