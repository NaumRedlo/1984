"""account message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
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

    # ── oauth (osu! OAuth callback — browser HTML page + link Telegram msg) ─
    "oauth.error_page": {
        "en": "<h2>Authorization error</h2><p>Try again via the bot.</p>",
        "ru": "<h2>Ошибка авторизации</h2><p>Попробуйте снова через бота.</p>",
    },
    "oauth.bad_request": {"en": "<h2>Invalid request</h2>", "ru": "<h2>Неверный запрос</h2>"},
    "oauth.link_expired": {
        "en": "<h2>Link expired</h2><p>Use the link command again.</p>",
        "ru": "<h2>Ссылка устарела</h2><p>Используйте команду link заново.</p>",
    },
    "oauth.token_error": {
        "en": "<h2>Couldn't get a token</h2><p>Try again.</p>",
        "ru": "<h2>Ошибка получения токена</h2><p>Попробуйте снова.</p>",
    },
    "oauth.user_fetch_failed": {
        "en": "<h2>Couldn't fetch osu! data</h2>",
        "ru": "<h2>Не удалось получить данные osu!</h2>",
    },
    "oauth.not_registered": {
        "en": ("<h2>Register first</h2>"
               "<p>Use the <code>register</code> command in the bot, then <code>link</code>.</p>"),
        "ru": ("<h2>Сначала зарегистрируйтесь</h2>"
               "<p>Используйте команду <code>register</code> в боте, затем <code>link</code>.</p>"),
    },
    "oauth.account_conflict": {
        "en": ("<h2>Account conflict</h2>"
               "<p>Your Telegram is linked to osu! ID {other_id}, "
               "but you authorised as {username} (ID {osu_id}).</p>"
               "<p>Use <code>unlink</code>, then <code>register</code> again.</p>"),
        "ru": ("<h2>Конфликт аккаунтов</h2>"
               "<p>Ваш Telegram привязан к osu! ID {other_id}, "
               "но вы авторизовались как {username} (ID {osu_id}).</p>"
               "<p>Используйте <code>unlink</code>, затем <code>register</code> заново.</p>"),
    },
    "oauth.success_page": {
        "en": ("<h2>Linked successfully!</h2>"
               "<p>Account <b>{username}</b> linked.</p>"
               "<p>You can return to Telegram.</p>"),
        "ru": ("<h2>Привязка успешна!</h2>"
               "<p>Аккаунт <b>{username}</b> привязан.</p>"
               "<p>Можете вернуться в Telegram.</p>"),
    },
    "oauth.notify_linked": {
        "en": "Account <b>{username}</b> successfully linked to the system.",
        "ru": "Аккаунт <b>{username}</b> успешно привязан к системе.",
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
}
