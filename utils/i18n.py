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

Admin/owner-only text and dev logs are intentionally NOT localised — they
stay in Russian in their own handlers.
"""

from __future__ import annotations

from typing import Dict

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "ru")

# key -> {lang -> template}
_CATALOG: Dict[str, Dict[str, str]] = {
    # ── common (shared across handlers) ──────────────────────────────────
    "common.api_not_ready": {
        "en": "Error: API client is not initialised.",
        "ru": "Ошибка: API-клиент не инициализирован.",
    },
    "common.loading": {
        "en": "Loading…",
        "ru": "Загрузка данных...",
    },
    "common.user_not_found": {
        "en": "User <b>{name}</b> was not found on osu!.",
        "ru": "Пользователь <b>{name}</b> не найден в базе osu!.",
    },
    "common.user_not_registered": {
        "en": "User <b>{name}</b> exists on osu! but isn't registered in the bot.",
        "ru": "Пользователь <b>{name}</b> найден в osu!, но не зарегистрирован в боте.",
    },
    "common.title_unlocked": {
        "en": "🏅 <b>{user}</b> — new title: {title} ({rarity})!",
        "ru": "🏅 <b>{user}</b> — новый титул: {title} ({rarity})!",
    },

    # shared inline-button labels
    "common.kb.leaderboard": {"en": "🏆 Leaderboard", "ru": "🏆 Топ карты"},
    "common.kb.beatmap": {"en": "Beatmap", "ru": "Карта"},
    "common.kb.render": {"en": "🎬 Render", "ru": "🎬 Рендер"},

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

    # ── cmp (compare command) ────────────────────────────────────────────
    "cmp.usage": {
        "en": ("Usage: <code>cmp &lt;nickname or id&gt;</code>\n"
               "Or: <code>cmp user1 vs user2</code>\n"
               "With a single player, the comparison is against your own profile."),
        "ru": ("Использование: <code>cmp &lt;никнейм или id&gt;</code>\n"
               "Или: <code>cmp user1 vs user2</code>\n"
               "Если указан один игрок, сравнение идёт с вашим профилем."),
    },
    "cmp.parse_failed": {
        "en": "Couldn't parse the comparison query.",
        "ru": "Не удалось разобрать запрос сравнения.",
    },
    "cmp.same_player": {
        "en": "Can't compare a player with themselves.",
        "ru": "Нельзя сравнивать одного и того же игрока.",
    },
    "cmp.error": {
        "en": "An error occurred while comparing.",
        "ru": "Произошла ошибка при сравнении.",
    },
    "cmp.text": {
        "en": ("<b>Comparison: {u1} vs {u2}</b>\n"
               "{sep}\n\n"
               "<b>PP:</b>\n"
               "  • {u1}: <code>{pp1}</code> ({ppd} PP)\n"
               "  • {u2}: <code>{pp2}</code>\n\n"
               "<b>Global rank:</b>\n"
               "  • {u1}: <code>#{rank1}</code> ({rankd} positions)\n"
               "  • {u2}: <code>#{rank2}</code>\n\n"
               "<b>Accuracy:</b>\n"
               "  • {u1}: <code>{acc1}%</code> ({accd})\n"
               "  • {u2}: <code>{acc2}%</code>\n\n"
               "<b>Play count:</b>\n"
               "  • {u1}: <code>{pc1}</code>\n"
               "  • {u2}: <code>{pc2}</code>"),
        "ru": ("<b>Сравнение: {u1} vs {u2}</b>\n"
               "{sep}\n\n"
               "<b>PP:</b>\n"
               "  • {u1}: <code>{pp1}</code> ({ppd} PP)\n"
               "  • {u2}: <code>{pp2}</code>\n\n"
               "<b>Глобальный ранг:</b>\n"
               "  • {u1}: <code>#{rank1}</code> ({rankd} позиций)\n"
               "  • {u2}: <code>#{rank2}</code>\n\n"
               "<b>Точность:</b>\n"
               "  • {u1}: <code>{acc1}%</code> ({accd})\n"
               "  • {u2}: <code>{acc2}%</code>\n\n"
               "<b>Количество игр:</b>\n"
               "  • {u1}: <code>{pc1}</code>\n"
               "  • {u2}: <code>{pc2}</code>"),
    },
}


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
