"""misc message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
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
