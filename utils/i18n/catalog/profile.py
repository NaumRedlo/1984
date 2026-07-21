"""profile message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
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

    # ── pf (profile dashboard) / rf (refresh) ────────────────────────────
    "pf.kb.osu_profile": {"en": "🔗 osu! profile", "ru": "🔗 Профиль osu!"},
    "pf.kb.top_plays": {"en": "🏆 Top plays", "ru": "🏆 Топ-плеи"},
    "pf.user_not_found": {
        "en": "User <b>{name}</b> was not found on osu!.",
        "ru": "Пользователь <b>{name}</b> не найден в osu!.",
    },
    "pf.refreshing": {
        "en": "Fetching fresh data from osu!…",
        "ru": "Загрузка свежих данных из osu!...",
    },
    "pf.refresh_failed_cached": {
        "en": "Couldn't fetch data from the osu! API. Showing cached data.",
        "ru": "Не удалось получить данные из osu! API. Показаны кешированные данные.",
    },
    "pf.card_gen_failed": {
        "en": "Error generating the profile card.",
        "ru": "Ошибка генерации карточки профиля.",
    },
    "pf.load_error": {
        "en": "An error occurred while loading the profile.",
        "ru": "Произошла ошибка при загрузке профиля.",
    },
    "rf.loading": {
        "en": "Fetching data from the osu! API…\n\n<i>This may take a few seconds</i>",
        "ru": "Загрузка данных из osu! API...\n\n<i>Это может занять несколько секунд</i>",
    },
    "rf.success": {
        "en": "<b>Data updated successfully!</b>",
        "ru": "<b>Данные успешно обновлены!</b>",
    },
    "rf.failed": {
        "en": "Couldn't update data. Try again later.",
        "ru": "Не удалось обновить данные. Попробуйте позже.",
    },
    "rf.error": {
        "en": "An error occurred while refreshing. Check the logs.",
        "ru": "Произошла ошибка при обновлении. Проверьте логи.",
    },

    # ── tpp (top plays) ──────────────────────────────────────────────────
    "tpp.kb.page": {"en": "Page {page}/{total}", "ru": "Стр. {page}/{total}"},
    "tpp.kb.back_to_profile": {"en": "◀ Back to profile", "ru": "◀ Назад к профилю"},
    "tpp.refreshing_cached_fallback": {
        "en": "Couldn't refresh, showing cached data.",
        "ru": "Не удалось обновить, показаны кешированные данные.",
    },
    "tpp.load_error": {
        "en": "An error occurred while loading top plays.",
        "ru": "Произошла ошибка при загрузке топ-плеев.",
    },
    "tpp.not_your_plays": {
        "en": "These aren't your top plays.",
        "ru": "Не ваши топ-плеи.",
    },
    "tpp.stale": {
        "en": "Expired — run tpp again.",
        "ru": "Устарело — запустите tpp снова.",
    },
    "tpp.not_your_profile": {
        "en": "Not your profile.",
        "ru": "Не ваш профиль.",
    },
    "tpp.profile_not_found": {
        "en": "Profile not found.",
        "ru": "Профиль не найден.",
    },

    # ── rs (recent play) ──────────────────────────────────────────────────
    "rs.searching_player": {
        "en": "Searching for player <b>{name}</b>…",
        "ru": "Поиск игрока <b>{name}</b>...",
    },
    "rs.player_not_found": {
        "en": "Player <b>{name}</b> not found.",
        "ru": "Игрок <b>{name}</b> не найден.",
    },
    "rs.search_error": {
        "en": "Error while searching for player <b>{name}</b>.",
        "ru": "Ошибка при поиске игрока <b>{name}</b>.",
    },
    "rs.loading": {
        "en": "Loading the last play of <b>{name}</b>…",
        "ru": "Загрузка последней игры <b>{name}</b>...",
    },
    "rs.no_recent_plays": {
        "en": "<b>{name}</b> has no recent plays in the last 24h.",
        "ru": "У <b>{name}</b> нет недавних игр за последние 24ч.",
    },
    "rs.fallback_text": {
        "en": ("<b>{name}'s last play</b>\n"
               "<b>{artist} - {title}</b>\n"
               "<i>[{version}]</i>{mods} ({stars:.2f}★)\n"
               "{sep}\n"
               "<b>Rank:</b> {rank} | <b>Accuracy:</b> {acc:.2f}%\n"
               "<b>Combo:</b> {combo}x{miss_or_fc}\n"
               "{pp_line}"),
        "ru": ("<b>Последняя игра {name}</b>\n"
               "<b>{artist} - {title}</b>\n"
               "<i>[{version}]</i>{mods} ({stars:.2f}★)\n"
               "{sep}\n"
               "<b>Ранг:</b> {rank} | <b>Точность:</b> {acc:.2f}%\n"
               "<b>Комбо:</b> {combo}x{miss_or_fc}\n"
               "{pp_line}"),
    },
    "rs.misses": {"en": " ({n} misses)", "ru": " ({n} миссов)"},
    "rs.fc": {"en": " (FC)", "ru": " (FC)"},
    "rs.titles_unlocked": {
        "en": "🏅 <b>{user}</b> — new title: {titles}!",
        "ru": "🏅 <b>{user}</b> — новый титул: {titles}!",
    },
    "rs.fetch_failed": {
        "en": "Couldn't fetch the last score from the osu! API.",
        "ru": "Не удалось получить последний скор из osu! API.",
    },
}
