"""settings message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
    # ── sts (settings menu) ────────────────────────────────────────────────
    "sts.foreign_menu": {
        "en": "This isn't your menu. Open your own: sts",
        "ru": "Это не ваше меню. Откройте своё: sts",
    },
    "sts.home": {"en": "⚙️ <b>Settings</b>\n\nPick a section:", "ru": "⚙️ <b>Настройки</b>\n\nВыберите раздел:"},
    "sts.kb.account": {"en": "👤 Account", "ru": "👤 Аккаунт"},
    "sts.kb.title": {"en": "🏅 Title", "ru": "🏅 Титул"},
    "sts.kb.language": {"en": "🌐 Language", "ru": "🌐 Язык"},
    "sts.kb.render": {"en": "🎬 Render", "ru": "🎬 Рендер"},

    # ── render section ───────────────────────────────────────────────────
    "sts.rnd.body": {
        "en": "<b>Render</b>\nHow replay videos are made: {summary}",
        "ru": "<b>Рендер</b>\nКак собирается видео реплея: {summary}",
    },
    "sts.rnd.size": {"en": "Size:", "ru": "Размер:"},
    "sts.rnd.fps": {"en": "Frames:", "ru": "Кадры:"},
    "sts.rnd.mute": {"en": "Sound:", "ru": "Звук:"},
    "sts.rnd.sound_on": {"en": "with sound", "ru": "со звуком"},
    "sts.rnd.sound_off": {"en": "muted", "ru": "без звука"},
    "sts.rnd.unknown": {"en": "No such setting.", "ru": "Такой настройки нет."},
    "sts.rnd.skin": {"en": "Skin — send an .osk to add one:",
                     "ru": "Скин — пришли .osk, чтобы добавить:"},
    "sts.rnd.skin_default": {"en": "the engine's own", "ru": "собственный движка"},
    "sts.rnd.skin_gone": {
        "en": "That skin is no longer stored — send it again.",
        "ru": "Этого скина больше нет — пришли его заново.",
    },
    # Named for what it does rather than for how it feels. Somebody reading
    # this once, quickly, has to come away knowing a file leaves their hands.
    "sts.rnd.share": {
        "en": "Send replay data to the developer",
        "ru": "Отправлять данные реплея разработчику",
    },
    "sts.rnd.share_on": {
        "en": ("On. Every replay you render is sent to the bot's author — the "
               ".osr file itself and what the engine made of it. Used to find "
               "where the engine judges a play wrongly. Turn it off here at any "
               "time; it changes nothing else."),
        "ru": ("Включено. Каждый отрендеренный реплей уходит автору бота — сам "
               "файл .osr и то, что о нём сказал движок. Нужно, чтобы находить "
               "места, где движок судит неверно. Выключить можно здесь в любой "
               "момент, на остальное это не влияет."),
    },
    "sts.rnd.share_off": {
        "en": "Off. Nothing is sent.",
        "ru": "Выключено. Ничего не отправляется.",
    },
    "sts.rnd.share_needs_account": {
        "en": "Link an osu! account first — there is nowhere to keep this yet.",
        "ru": "Сначала привяжи аккаунт osu! — это пока негде сохранить.",
    },
    "sts.kb.close": {"en": "Close", "ru": "Закрыть"},
    "sts.kb.back": {"en": "‹ Back", "ru": "‹ Назад"},
    "sts.not_registered": {"en": "You aren't registered. register [name]", "ru": "Вы не зарегистрированы. register [ник]"},



    "sts.page_suffix": {"en": "  ({page}/{total})", "ru": "  (стр. {page}/{total})"},



    "sts.acc.not_linked": {
        "en": "👤 <b>Account</b>\n\nosu! isn't linked.\nRegister in a group chat: <code>register [name]</code>",
        "ru": "👤 <b>Аккаунт</b>\n\nosu! не привязан.\nЗарегистрируйтесь в беседе: <code>register [ник]</code>",
    },
    "sts.acc.linked": {
        "en": "👤 <b>Account</b>\n\nosu!: <b>{name}</b>\nOAuth: {status}",
        "ru": "👤 <b>Аккаунт</b>\n\nosu!: <b>{name}</b>\nOAuth: {status}",
    },
    "sts.acc.oauth_yes": {"en": "✅ linked", "ru": "✅ привязан"},
    "sts.acc.oauth_no": {"en": "❌ not linked", "ru": "❌ не привязан"},
    "sts.kb.relink": {"en": "🔁 Re-link osu!", "ru": "🔁 Перепривязать osu!"},
    "sts.kb.link": {"en": "🔗 Link osu!", "ru": "🔗 Привязать osu!"},
    "sts.kb.unlink": {"en": "❌ Unlink account", "ru": "❌ Отвязать аккаунт"},
    "sts.acc.relink_title": {"en": "🔁 Re-linking osu!", "ru": "🔁 Перепривязка osu!"},
    "sts.acc.link_title": {"en": "🔗 Linking osu!", "ru": "🔗 Привязка osu!"},
    "sts.acc.oauth_prompt": {
        "en": ("{title}\n\n"
               "Open the link and authorise:\n"
               "<a href=\"{url}\">Authorise in osu!</a>\n\n"
               "Return to Telegram afterwards."),
        "ru": ("{title}\n\n"
               "Откройте ссылку и авторизуйтесь:\n"
               "<a href=\"{url}\">Авторизоваться в osu!</a>\n\n"
               "После авторизации вернитесь в Telegram."),
    },
    "sts.acc.link_sent": {"en": "Link sent below ⬇️", "ru": "Ссылка отправлена ниже ⬇️"},
    "sts.acc.unlink_confirm": {
        "en": ("⚠️ <b>Unlink your osu! account?</b>\n\n"
               "This deletes: the link, OAuth, titles and cached scores.\n"
               "Unlinking again is available once a month."),
        "ru": ("⚠️ <b>Отвязать osu! аккаунт?</b>\n\n"
               "Будут удалены: привязка, OAuth, титулы и кэш скоров.\n"
               "Повторная отвязка доступна раз в месяц."),
    },
    "sts.kb.confirm_unlink": {"en": "⚠️ Yes, unlink", "ru": "⚠️ Да, отвязать"},
    "sts.kb.cancel_back": {"en": "‹ Cancel", "ru": "‹ Отмена"},
    "sts.acc.not_linked_alert": {"en": "Account isn't linked.", "ru": "Аккаунт не привязан."},
    "sts.acc.unlink_cooldown": {
        "en": "Unlinking is available once a month. Try again in {remaining}.",
        "ru": "Отвязка раз в месяц. Повторите через {remaining}.",
    },
    "sts.acc.unlinked": {
        "en": "✅ osu! account unlinked. You can unlink again in a month.",
        "ru": "✅ Аккаунт osu! отвязан. Повторная отвязка доступна через месяц.",
    },
    "sts.done": {"en": "Done", "ru": "Готово"},

    "sts.lang.view": {
        "en": "🌐 <b>Language</b>\n\nCurrent: <b>{current}</b>\nAffects text drawn on cards.",
        "ru": "🌐 <b>Язык</b>\n\nТекущий: <b>{current}</b>\nВлияет на текст, нарисованный на карточках.",
    },
    "sts.lang.set_alert": {"en": "Language: {lang}", "ru": "Язык: {lang}"},

    "sts.title.header": {"en": "🏅 <b>Title</b>\n\nActive: <b>{name}</b>\n\n", "ru": "🏅 <b>Титул</b>\n\nАктивный: <b>{name}</b>\n\n"},
    "sts.title.none": {"en": "— none —", "ru": "— нет —"},
    "sts.title.no_unlocked": {
        "en": "No unlocked titles yet. Unlock them by playing — <code>tt</code>.",
        "ru": "Пока нет открытых титулов. Открывайте их игрой — <code>tt</code>.",
    },
    "sts.title.pick": {"en": "Pick a title for your profile:", "ru": "Выберите титул для профиля:"},
    "sts.kb.clear_title": {"en": "Clear title", "ru": "Снять титул"},
    "sts.title.not_unlocked": {"en": "This title isn't unlocked yet.", "ru": "Этот титул ещё не открыт."},
    "sts.title.set_alert": {"en": "★ {name}", "ru": "★ {name}"},


    # ── dm_tenant (DM group picker) ───────────────────────────────────────
    "dm.no_groups": {
        "en": ("You aren't registered in any group chat yet.\n"
               "Go to a chat with the bot and send <code>register &lt;nickname&gt;</code>, "
               "then come back here."),
        "ru": ("Вы пока не зарегистрированы ни в одной беседе.\n"
               "Зайдите в беседу с ботом и отправьте <code>register &lt;ник&gt;</code>, "
               "затем вернитесь сюда."),
    },
    "dm.using_group": {
        "en": "Using data from <b>{label}</b>.\nChange it later with <code>group</code>.",
        "ru": "Использую данные беседы <b>{label}</b>.\nСменить позже — команда <code>group</code>.",
    },
    "dm.pick_group": {
        "en": "Which group should your data come from? Pick one:",
        "ru": "В какой беседе показывать ваши данные? Выберите группу:",
    },
    "dm.pick_first": {"en": "Pick a group first.", "ru": "Сначала выберите беседу."},
    "dm.bad_choice": {"en": "Invalid choice.", "ru": "Некорректный выбор."},
    "dm.group_unavailable": {"en": "That group isn't available.", "ru": "Эта беседа недоступна."},
    "dm.done": {"en": "Done.", "ru": "Готово."},
    "dm.switched": {
        "en": ("Using data from <b>{label}</b>.\n"
               "Change it later with <code>group</code>.\n"
               "Now repeat your command."),
        "ru": ("Использую данные беседы <b>{label}</b>.\n"
               "Сменить позже — команда <code>group</code>.\n"
               "Теперь повторите свою команду."),
    },
}
