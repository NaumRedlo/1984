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

    # format_error / format_success prefixes
    "common.error_prefix": {"en": "Error! ", "ru": "Ошибка! "},
    "common.success_prefix": {"en": "Success! ", "ru": "Успешно! "},
    "common.duration_dh": {"en": "{days}d {hours}h", "ru": "{days}д {hours}ч"},

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

    # ── help (help menu) ─────────────────────────────────────────────────
    "help.home": {
        "en": ("📖 <b>Help — Project 1984</b>\n\n"
               "Pick a section to see its commands:"),
        "ru": ("📖 <b>Справка — Project 1984</b>\n\n"
               "Выберите раздел, чтобы посмотреть команды:"),
    },
    "help.btn.close": {"en": "Close", "ru": "Закрыть"},
    "help.btn.back": {"en": "‹ Back", "ru": "‹ Назад"},
    "help.sec.osu.label": {"en": "🎮 osu!", "ru": "🎮 osu!"},
    "help.sec.osu.body": {
        "en": ("🎮 <b>osu! commands</b>\n\n"
               "<code>pf</code> — stats & rank card\n"
               "<code>rs</code> — last played beatmap\n"
               "<code>cmp [name]</code> — compare stats with a player\n"
               "<code>lb</code> — leaderboard\n"
               "<code>lbm [id/link]</code> — local map leaderboard\n"
               "🎬 button under the <code>rs</code> card — render the replay to video\n"
               "<code>tt</code> — title collection\n"
               "<code>rf</code> — sync with the osu! API"),
        "ru": ("🎮 <b>Команды osu!</b>\n\n"
               "<code>pf</code> — карточка статы и ранга\n"
               "<code>rs</code> — последняя сыгранная карта\n"
               "<code>cmp [ник]</code> — сравнить статы с игроком\n"
               "<code>lb</code> — лидерборд\n"
               "<code>lbm [id/ссылка]</code> — локальный лидерборд карты\n"
               "🎬 кнопка под карточкой <code>rs</code> — рендер реплея в видео\n"
               "<code>tt</code> — коллекция титулов\n"
               "<code>rf</code> — синхронизация с osu! API"),
    },
    # ── reg / link / relink / unlink (account commands) ──────────────────
    "reg.usage": {
        "en": ("<b>Enter your osu! nickname or ID:</b>\n"
               "<code>register Nickname</code> or <code>register id:12345</code>"),
        "ru": ("<b>Укажите ваш osu! никнейм или ID:</b>\n"
               "<code>register Nickname</code> или <code>register id:12345</code>"),
    },
    "reg.groups_only": {
        "en": "Registration is only available inside a group chat.",
        "ru": "Регистрация доступна только внутри беседы.",
    },
    "reg.searching": {
        "en": "Searching osu!: <b>{name}</b>…",
        "ru": "Поиск в базе osu!: <b>{name}</b>...",
    },
    "reg.osu_taken": {
        "en": "osu! account <b>{name}</b> is already linked to another user.",
        "ru": "Аккаунт osu! <b>{name}</b> уже привязан к другому пользователю.",
    },
    "reg.already_linked": {
        "en": ("Your profile is already linked to <b>{name}</b>.\n"
               "Re-linking is admin-only."),
        "ru": ("Ваш профиль уже привязан к <b>{name}</b>.\n"
               "Перепривязка доступна только администраторам."),
    },
    "reg.action.registered": {"en": "registered", "ru": "зарегистрирован"},
    "reg.action.relinked": {"en": "re-linked", "ru": "перепривязан"},
    "reg.success": {
        "en": ("<b>Identity confirmed!</b>\n\n"
               "User <code>{name}</code> {action} in the Project 1984 system.\n"
               "Rank: <code>#{rank}</code>\n"
               "PP: <code>{pp}</code>"),
        "ru": ("<b>Личность подтверждена!</b>\n\n"
               "Пользователь <code>{name}</code> {action} в системе Project 1984.\n"
               "Ранг: <code>#{rank}</code>\n"
               "PP: <code>{pp}</code>"),
    },
    "reg.sys_error": {
        "en": "System error during verification.",
        "ru": "Системная ошибка при верификации.",
    },
    "reg.lang.not_yours": {
        "en": "This isn't your choice.",
        "ru": "Это не ваш выбор.",
    },
    "reg.lang.set": {
        "en": "Card language: <b>{label}</b>. Change it in sts.",
        "ru": "Язык карточек: <b>{label}</b>. Изменить можно в sts.",
    },
    "link.need_register": {
        "en": "Register in a group chat first: <code>register &lt;nickname&gt;</code>",
        "ru": "Сначала зарегистрируйтесь в беседе: <code>register &lt;nickname&gt;</code>",
    },
    "link.already_linked": {
        "en": ("Account <b>{name}</b> is already linked to the system.\n"
               "If the token is broken and you need to re-link, use <code>relink</code>."),
        "ru": ("Аккаунт <b>{name}</b> уже привязан к системе.\n"
               "Если токен сломан и нужно перепривязать — используй <code>relink</code>."),
    },
    "link.prompt": {
        "en": ("🔗 <b>Link osu! OAuth</b>\n\n"
               "Open the link and authorise:\n"
               "<a href=\"{url}\">Authorise in osu!</a>\n\n"
               "Return to Telegram afterwards."),
        "ru": ("🔗 <b>Привязка osu! OAuth</b>\n\n"
               "Перейдите по ссылке и авторизуйтесь:\n"
               "<a href=\"{url}\">Авторизоваться в osu!</a>\n\n"
               "После авторизации вернитесь в Telegram."),
    },
    "relink.prompt": {
        "en": ("🔁 <b>Re-link osu! OAuth</b>\n\n"
               "The old token was removed. Progress, ratings and history are <b>kept</b>.\n\n"
               "Open the link and authorise again:\n"
               "<a href=\"{url}\">Authorise in osu!</a>\n\n"
               "Return to Telegram afterwards — everything will work again."),
        "ru": ("🔁 <b>Перепривязка osu! OAuth</b>\n\n"
               "Старый токен удалён. Прогресс, рейтинги и история <b>сохранены</b>.\n\n"
               "Открой ссылку и авторизуйся заново:\n"
               "<a href=\"{url}\">Авторизоваться в osu!</a>\n\n"
               "После авторизации вернись в Telegram — всё снова заработает."),
    },
    "unlink.not_linked": {
        "en": "Your profile isn't linked to an osu! account.",
        "ru": "Ваш профиль не привязан к osu! аккаунту.",
    },
    "unlink.cooldown": {
        "en": "Unlinking is available once a month. Try again in {remaining}.",
        "ru": "Отвязка доступна раз в месяц. Повторите через {remaining}.",
    },
    "unlink.success": {
        "en": "osu! account link removed. You can unlink again in a month.",
        "ru": "Привязка osu! аккаунта удалена. Повторная отвязка доступна через месяц.",
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

    # ── auth (registration / oauth gates) ────────────────────────────────
    "auth.not_registered": {
        "en": ("You're not registered in this chat.\n"
               "Use <code>register &lt;osu_nickname&gt;</code>"),
        "ru": ("Вы не зарегистрированы в этой беседе.\n"
               "Используйте <code>register &lt;osu_nickname&gt;</code>"),
    },
    "auth.not_registered_alert": {
        "en": "Register in this chat first.",
        "ru": "Сначала зарегистрируйтесь в этой беседе.",
    },
    "auth.link_first": {
        "en": "Link osu! OAuth first: <code>link</code>",
        "ru": "Сначала привяжите osu! OAuth: <code>link</code>",
    },
    "auth.link_first_alert": {
        "en": "Link osu! OAuth via link first.",
        "ru": "Сначала привяжите osu! OAuth через link.",
    },
    "auth.not_your_card": {
        "en": "This isn't your card.",
        "ru": "Это не ваша карточка.",
    },

    # ── start (welcome) ──────────────────────────────────────────────────
    "start.welcome": {
        "en": ("<b>PROJECT 1984: CLASSIFIED</b>\n"
               "{sep}\n\n"
               "Welcome, <b>{name}</b>.\n"
               "You've been granted access to the surveillance system.\n\n"
               "<b>Quick start:</b>\n"
               "• <code>register [nickname]</code> — Link your osu! account\n"
               "• <code>pf</code> — Stats and rank\n"
               "• <code>rs</code> — Last played beatmap\n"
               "• <code>tpp</code> — Top plays\n"
               "• <code>tt</code> — Title collection\n"
               "• <code>cmp [player]</code> — Compare stats\n"
               "• <code>lb</code> — Leaderboard\n"
               "• <code>help</code> — Full list of directives\n\n"
               "<i>Big Brother is watching your rank.</i>"),
        "ru": ("<b>PROJECT 1984: CLASSIFIED</b>\n"
               "{sep}\n\n"
               "Добро пожаловать, <b>{name}</b>.\n"
               "Вам предоставлен доступ к системе наблюдения.\n\n"
               "<b>Быстрый старт:</b>\n"
               "• <code>register [никнейм]</code> — Привязать osu! аккаунт\n"
               "• <code>pf</code> — Статистика и ранг\n"
               "• <code>rs</code> — Последняя сыгранная карта\n"
               "• <code>tpp</code> — Топ-плеи\n"
               "• <code>tt</code> — Коллекция титулов\n"
               "• <code>cmp [игрок]</code> — Сравнение статистики\n"
               "• <code>lb</code> — Таблица лидеров\n"
               "• <code>help</code> — Полный список директив\n\n"
               "<i>Большой Брат следит за вашим рангом.</i>"),
    },

    "help.sec.account.label": {"en": "👤 Account", "ru": "👤 Аккаунт"},
    "help.sec.account.body": {
        "en": ("👤 <b>Account</b>\n\n"
               "<code>reg [name]</code> — register in the system\n"
               "<code>link</code> — link osu! via OAuth\n"
               "<code>relink</code> — re-link OAuth (keeps your progress)\n"
               "<code>unlink</code> — unlink the account (30-day cooldown)\n"
               "<code>sts</code> — bot settings\n"
               "<code>start</code> / <code>help</code> — greeting / this help"),
        "ru": ("👤 <b>Аккаунт</b>\n\n"
               "<code>reg [ник]</code> — регистрация в системе\n"
               "<code>link</code> — привязать osu! через OAuth\n"
               "<code>relink</code> — перепривязать OAuth (без потери прогресса)\n"
               "<code>unlink</code> — отвязать аккаунт (кулдаун 30 дней)\n"
               "<code>sts</code> — настройки бота\n"
               "<code>start</code> / <code>help</code> — приветствие / эта справка"),
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
