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

    # ── lb: weekly growth ("delta") mode ─────────────────────────────────
    "lb.mode.delta": {"en": "📈 Growth", "ru": "📈 Прирост"},
    "lb.mode.absolute": {"en": "📊 All-time", "ru": "📊 Всего"},
    "lb.delta.title": {"en": "Leaderboard · growth", "ru": "Лидерборд · прирост"},
    "lb.abs.title": {"en": "Leaderboard · all-time", "ru": "Лидерборд · всего"},
    "lb.abs.subtitle": {"en": "lifetime standings", "ru": "за всё время"},
    "lb.abs.empty": {"en": "no data yet", "ru": "данных пока нет"},
    "lb.delta.period": {"en": "week {week} · {span}", "ru": "неделя {week} · {span}"},
    # Russian needs three plural forms; English collapses to two.
    "lb.delta.participants.one": {"en": "{n} participant", "ru": "{n} участник"},
    "lb.delta.participants.few": {"en": "{n} participants", "ru": "{n} участника"},
    "lb.delta.participants.many": {"en": "{n} participants", "ru": "{n} участников"},
    "lb.delta.total": {"en": "{value} total", "ru": "{value} всего"},
    "lb.delta.new": {"en": "NEW", "ru": "NEW"},
    "lb.delta.gap": {"en": "{value} to place {place}", "ru": "до {place}-го места {value}"},
    "lb.delta.no_gain": {
        "en": "no one has gained anything yet this week",
        "ru": "на этой неделе прироста пока ни у кого",
    },
    "lb.delta.sat_out": {"en": "· {n} without gains", "ru": "· {n} без прироста"},
    "lb.delta.collecting": {
        "en": "collecting data — first standings on {date}",
        "ru": "идёт сбор данных — первый зачёт {date}",
    },
    "lb.delta.updated": {"en": "updated {time} MSK", "ru": "обновлено {time} MSK"},


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
