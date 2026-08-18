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
    # Said on the screen rather than only at the moment of refusal: somebody
    # picking 4K should know what it costs before they go looking for a video.
    "sts.rnd.ration": {
        "en": "Above 1080p60: {left} of {total} left today.",
        "ru": "Выше 1080p60: осталось {left} из {total} на сегодня.",
    },
    "sts.rnd.ration_needs_account": {
        "en": "4K and 120 fps are counted per day, and the count needs a linked "
              "account. Link one and pick it again.",
        "ru": "4K и 120 fps считаются по дням, а счёт нужно где-то хранить — "
              "привяжи аккаунт и выбери заново.",
    },
    "sts.rnd.ration_spent": {
        "en": "Today's five renders above 1080p60 are used up. Anything at or "
              "below 1080p60 is unlimited.",
        "ru": "Пять сегодняшних рендеров выше 1080p60 израсходованы. Всё до "
              "1080p60 включительно — без ограничений.",
    },
    # The three switches. Each is worded so that a tick means the thing named
    # is *on* — "Sound:" beside a ticked box says nothing at all.
    "sts.rnd.mute": {"en": "Muted", "ru": "Без звука"},
    "sts.rnd.background": {"en": "Map artwork", "ru": "Фон карты"},
    "sts.rnd.bare": {"en": "No interface", "ru": "Без интерфейса"},
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
    # ── render sub-tabs (the engine's optional movements) ────────────────
    "sts.fx.body": {
        "en": "<b>Fine tuning — {tab}</b>\nWhat moves, and what stays still.",
        "ru": "<b>Тонкая настройка — {tab}</b>\nЧто движется, а что стоит на месте.",
    },
    "sts.fx.tab.slider": {"en": "🎢 Sliders", "ru": "🎢 Слайдеры"},
    "sts.fx.tab.cursor": {"en": "🖱 Cursor", "ru": "🖱 Курсор"},
    "sts.fx.tab.note": {"en": "🎯 Notes", "ru": "🎯 Ноты"},
    # Each switch is named for what happens when it is ticked, so that the tick
    # and the words agree.
    "sts.fx.snake-in": {
        "en": "Body grows out of the head",
        "ru": "Тело выдвигается из головы",
    },
    "sts.fx.snake-out": {
        "en": "Body retracts behind the ball",
        "ru": "Тело задвигается за шариком",
    },
    "sts.fx.cursor-expand": {
        "en": "Cursor swells on a click",
        "ru": "Курсор растёт при нажатии",
    },
    "sts.fx.cursor-trail": {"en": "Cursor leaves a trail", "ru": "След за курсором"},
    "sts.fx.hit-lighting": {
        "en": "Flash from a struck note",
        "ru": "Вспышка от попадания",
    },
    # Said on each screen rather than in a manual: these are the two things
    # somebody is choosing between, and the reason one of them is off by default
    # is that a render is *watched* rather than played.
    "sts.fx.about.slider": {
        "en": "Both tell a player something they need in the half second before "
              "they hit it. Off, for a viewer who has no such half second.",
        "ru": "Обе подсказки нужны игроку за полсекунды до удара. Выключены — "
              "зрителю эти полсекунды ни к чему.",
    },
    "sts.fx.about.cursor": {
        "en": "The swell shows a click that the keypad already shows. The trail "
              "shows where the cursor has been, and is on.",
        "ru": "Рост показывает нажатие, которое и так видно на кейпаде. След "
              "показывает, где курсор был, и включён.",
    },
    "sts.fx.about.note": {
        "en": "On a dense map each flash lasts more than a second, so a dozen "
              "are up at once and the play is behind them.",
        "ru": "На плотной карте каждая вспышка живёт больше секунды — их разом "
              "с десяток, и игра оказывается за ними.",
    },
    "sts.qly.tab": {"en": "🎞 Quality", "ru": "🎞 Качество"},
    "sts.qly.body": {
        "en": "<b>Fine tuning — quality</b>\nHow big and how smooth: {summary}",
        "ru": "<b>Тонкая настройка — качество</b>\nНасколько крупно и насколько "
              "плавно: {summary}",
    },
    "sts.snd.tab": {"en": "🔊 Sound", "ru": "🔊 Звук"},
    "sts.snd.body": {
        "en": "<b>Fine tuning — sound</b>\nHow loud each half of the mix is. "
              "The music already sits under the hit sounds; these are on top of "
              "that.",
        "ru": "<b>Тонкая настройка — звук</b>\nНасколько громка каждая половина "
              "микса. Музыка и так приглушена под хитсаунды — это поверх того.",
    },
    "sts.snd.music": {"en": "Music", "ru": "Музыка"},
    "sts.snd.hitsounds": {"en": "Hit sounds", "ru": "Хитсаунды"},
    "sts.snd.muted": {
        "en": "The render is muted, so neither level is heard. Untick "
              "<i>Muted</i> on the render screen first.",
        "ru": "Рендер без звука — ни один уровень не прозвучит. Сначала снимите "
              "<i>Без звука</i> на экране рендера.",
    },
    "sts.fx.now_on": {"en": "{name} — on", "ru": "{name} — включено"},
    "sts.fx.now_off": {"en": "{name} — off", "ru": "{name} — выключено"},
    "sts.fx.back": {"en": "← Render", "ru": "← Рендер"},

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
    # The toast, which Telegram caps at 200 characters — the full wording is
    # on the screen itself, where there is room for it and where somebody
    # wondering months later will actually look.
    "sts.rnd.share_agreed": {
        "en": "On. Your replays and the engine's reading of them are sent to the "
              "bot's author. Turn it off here at any time.",
        "ru": "Включено. Твои реплеи и разбор движка уходят автору бота. "
              "Выключить можно здесь в любой момент.",
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
