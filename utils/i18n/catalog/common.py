"""common message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
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

    # format_error / format_success prefixes
    "common.error_prefix": {"en": "Error! ", "ru": "Ошибка! "},
    "common.success_prefix": {"en": "Success! ", "ru": "Успешно! "},
    "common.duration_dh": {"en": "{days}d {hours}h", "ru": "{days}д {hours}ч"},
    "common.anon_name": {"en": "Citizen", "ru": "Гражданин"},
    "common.not_your_list": {"en": "Not your list.", "ru": "Это не ваш список."},
    "common.pages_stale": {
        "en": "Pages expired — run the command again.",
        "ru": "Страницы устарели — запросите команду снова.",
    },
    "common.group_only": {
        "en": "This command only works in a group chat.",
        "ru": "Эта команда работает только в беседе.",
    },
    "common.stale_repeat": {"en": "Expired — repeat the action.", "ru": "Устарело — повторите действие."},
    "common.something_wrong": {
        "en": "Something went wrong. Try again.",
        "ru": "Что-то пошло не так. Попробуй ещё раз.",
    },

    # shared inline-button labels
    "common.kb.leaderboard": {"en": "🏆 Leaderboard", "ru": "🏆 Топ карты"},
    "common.kb.beatmap": {"en": "Beatmap", "ru": "Карта"},
    "common.kb.render": {"en": "🎬 Render", "ru": "🎬 Рендер"},
}
