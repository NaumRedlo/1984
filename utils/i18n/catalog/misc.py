CATALOG = {
    # ── help (help menu) ─────────────────────────────────────────────────
    "help.home": {
        "en": ("<b>Help — Project 1984</b>\n\n"
               "Pick a section to see its commands:"),
        "ru": ("<b>Справка — Project 1984</b>\n\n"
               "Выберите раздел, чтобы посмотреть команды:"),
    },
    "help.btn.close": {"en": "Close", "ru": "Закрыть"},
    "help.btn.back": {"en": "‹ Back", "ru": "‹ Назад"},
    "help.sec.osu.label": {"en": "osu!", "ru": "osu!"},
    "help.sec.osu.body": {
        "en": ("<b>Basic commands</b>\n\n"
               "<code>pf</code> — your profile card\n"
               "<code>rs</code> — last played beatmap\n"
               "<code>tpp</code> — your top plays\n"
               "<code>cmp [name]</code> — compare your statistics with a player\n"
               "<code>lb</code> — group leaderboard\n"
               "<code>lbm [id/link]</code> — local map leaderboard\n"
               "<code>map &lt;accuracy&gt; [mods]</code> — what that score would "
               "be worth, in reply to a card\n"
               "<code>tt</code> — title collection\n"
               "<code>st &lt;title&gt;</code> — wear one, <code>st off</code> to "
               "take it off\n"
               "<code>rf</code> — forced synchronization with the osu! API"),
        "ru": ("<b>Основные команды</b>\n\n"
               "<code>pf</code> — карточка твоего профиля\n"
               "<code>rs</code> — последняя сыгранная карта\n"
               "<code>tpp</code> — твои топ-плеи\n"
               "<code>cmp [ник]</code> — сравнить статистику с игроком\n"
               "<code>lb</code> — лидерборд беседы\n"
               "<code>lbm [id/ссылка]</code> — локальный лидерборд карты\n"
               "<code>map &lt;точность&gt; [моды]</code> — сколько дал бы такой "
               "скор, ответом на карточку\n"
               "<code>tt</code> — коллекция титулов\n"
               "<code>st &lt;титул&gt;</code> — надеть титул, "
               "<code>st снять</code> — снять\n"
               "<code>rf</code> — принудительная синхронизация с osu! API"),
    },
    # ── start (welcome) ──────────────────────────────────────────────────
    "start.welcome": {
        "en": ("<b>1984 | Global & Competitive</b>\n"
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
        "ru": ("<b>1984 | Global & Competitive</b>\n"
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

    "help.sec.account.label": {"en": "Account", "ru": "Аккаунт"},
    "help.sec.account.body": {
        "en": ("<b>Account</b>\n\n"
               "<code>reg [name]</code> — sign up via the bot\n"
               "<code>link</code> — link osu! via OAuth\n"
               "<code>relink</code> — re-link OAuth (keeps your progress)\n"
               "<code>unlink</code> — unlink the account (30-day cooldown)\n"
               "<code>sts</code> — bot settings\n"
               "<code>group</code> / <code>switch</code> — pick which group a "
               "direct message counts for\n"
               "<code>start</code> / <code>help</code> — greeting / this help"),
        "ru": ("<b>Аккаунт</b>\n\n"
               "<code>reg [ник]</code> — регистрация в боте\n"
               "<code>link</code> — привязать osu! аккаунт через OAuth\n"
               "<code>relink</code> — перепривязать OAuth (без потери прогресса)\n"
               "<code>unlink</code> — отвязать аккаунт (откат 30 дней)\n"
               "<code>sts</code> — настройки бота\n"
               "<code>group</code> / <code>switch</code> — выбрать, за какую "
               "беседу считается переписка в личке\n"
               "<code>start</code> / <code>help</code> — приветствие / эта справка"),
    },

    # ── help: Dossier ────────────────────────────────────────────────────
    # Shown only to whoever the render gate lets through, the same as the
    # commands themselves: a category where every line answers "not for you"
    # is worse than no category.
    "help.sec.dossier.label": {"en": "Dossier", "ru": "Dossier"},
    "help.sec.dossier.body": {
        "en": ("<b>Dossier — replay rendering</b>\n\n"
               "Send a <code>.osr</code> as a file. The replay is read and "
               "judged, and then there is a choice:\n"
               "🎬 <b>Render</b> — the play as a video\n"
               "✂️ <b>Reel</b> — a short cut of it\n"
               "🗺 <b>Map</b> — the beatmap card\n"
               "🏆 <b>Map top</b> — the local leaderboard\n\n"
               "Send a <code>.osk</code> as a file to keep your own skin. "
               "Choose it — along with the size and the frame rate — in "
               "<code>sts</code> → Render.\n\n"
               "<code>/dossier</code> — whether the engine is up\n"
               "<code>rdrw</code> — who is on the farm right now\n"
               "<code>cltoken</code> — a join code for one machine (admins)\n\n"
               "<i>The rendering happens on the machines people lend, not on "
               "the bot's host. With nobody on the farm a job simply waits, and "
               "the video arrives by itself once somebody switches theirs "
               "on.</i>"),
        "ru": ("<b>Dossier — рендер реплеев</b>\n\n"
               "Пришли <code>.osr</code> файлом. Реплей будет разобран и "
               "отсужен, а дальше на выбор:\n"
               "🎬 <b>Отрендерить</b> — видео заезда\n"
               "✂️ <b>Экспозитор</b> — короткая нарезка\n"
               "🗺 <b>Карта</b> — карточка карты\n"
               "🏆 <b>Топ карты</b> — локальный лидерборд\n\n"
               "Пришли <code>.osk</code> файлом, чтобы сохранить свой скин. "
               "Выбрать его — вместе с размером и частотой кадров — в "
               "<code>sts</code> → Рендер.\n\n"
               "<code>/dossier</code> — на связи ли движок\n"
               "<code>rdrw</code> — кто сейчас на ферме\n"
               "<code>cltoken</code> — код на одну машину (админы)\n\n"
               "<i>Рендерят компьютеры, которые одалживают ребята, а не сервер "
               "бота. Если на ферме никого, задача просто ждёт, и видео придёт "
               "само, как только кто-нибудь включит свой.</i>"),
    },
}
