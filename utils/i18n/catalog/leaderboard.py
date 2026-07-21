"""leaderboard message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
    # ── lb / lbm (leaderboard) ───────────────────────────────────────────
    "lb.cat.pp": {"en": "PP/Rank", "ru": "PP/Ранг"},
    "lb.cat.accuracy": {"en": "Accuracy", "ru": "Точность"},
    "lb.cat.play_count": {"en": "Playcount", "ru": "Плейкаунт"},
    "lb.cat.play_time": {"en": "Time", "ru": "Время"},
    "lb.cat.ranked_score": {"en": "R. Score", "ru": "Р. очки"},
    "lb.cat.hits_per_play": {"en": "HPP", "ru": "ХПП"},
    "lb.cat.best_pp": {"en": "Top Score", "ru": "Топ скор"},
    "lb.load_error": {
        "en": "An error occurred while loading the leaderboard.",
        "ru": "Произошла ошибка при загрузке таблицы лидеров.",
    },
    "lb.bad_data": {"en": "Invalid data.", "ru": "Некорректные данные."},
    "lb.unknown_category": {"en": "Unknown category", "ru": "Неизвестная категория"},
    "lb.update_error": {"en": "Error updating the leaderboard", "ru": "Ошибка при обновлении лидерборда"},
    "lbm.usage": {
        "en": ("Usage:\n"
               "• <code>lbm</code> — as a reply to a recent-play card\n"
               "• <code>lbm 123456</code> — by map ID\n"
               "• <code>lbm https://osu.ppy.sh/beatmaps/...</code> — by link"),
        "ru": ("Использование:\n"
               "• <code>lbm</code> — в ответ на карточку recent\n"
               "• <code>lbm 123456</code> — по ID карты\n"
               "• <code>lbm https://osu.ppy.sh/beatmaps/...</code> — по ссылке"),
    },
    "lbm.loading": {"en": "Loading the leaderboard…", "ru": "Загрузка лидерборда..."},
    "lbm.no_plays": {
        "en": "No registered player has played this map yet.",
        "ru": "Эту карту ещё не сыграл ни один зарегистрированный пользователь.",
    },
    "lbm.build_failed": {
        "en": "Couldn't build the map leaderboard.",
        "ru": "Не удалось построить leaderboard по карте.",
    },
}
