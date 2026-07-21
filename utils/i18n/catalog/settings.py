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
    "sts.kb.render": {"en": "🎬 Replay render", "ru": "🎬 Рендер реплеев"},
    "sts.kb.my_renders": {"en": "📼 My renders", "ru": "📼 Мои рендеры"},
    "sts.kb.account": {"en": "👤 Account", "ru": "👤 Аккаунт"},
    "sts.kb.title": {"en": "🏅 Title", "ru": "🏅 Титул"},
    "sts.kb.language": {"en": "🌐 Language", "ru": "🌐 Язык"},
    "sts.kb.close": {"en": "Close", "ru": "Закрыть"},
    "sts.kb.back": {"en": "‹ Back", "ru": "‹ Назад"},
    "sts.not_registered": {"en": "You aren't registered. register [name]", "ru": "Вы не зарегистрированы. register [ник]"},

    "sts.render_home": {
        "en": "🎬 <b>Render settings</b>\n\nPick a category:",
        "ru": "🎬 <b>Настройки рендера</b>\n\nВыберите категорию:",
    },
    "sts.kb.video": {"en": "🎨 Video", "ru": "🎨 Видео"},
    "sts.kb.interface": {"en": "📊 Interface", "ru": "📊 Интерфейс"},
    "sts.kb.reset_render": {"en": "↺ Reset settings", "ru": "↺ Сбросить настройки"},

    "sts.video_home": {
        "en": "🎨 <b>Video</b>\n\nTap a parameter to change it:",
        "ru": "🎨 <b>Видео</b>\n\nНажмите параметр, чтобы изменить его:",
    },
    "sts.ui_home": {
        "en": "📊 <b>Interface</b>\n\nTap an item to turn it on/off:",
        "ru": "📊 <b>Интерфейс</b>\n\nНажмите элемент, чтобы вкл/выкл:",
    },
    "sts.toggle.pp": {"en": "PP counter", "ru": "PP-счётчик"},
    "sts.toggle.sb": {"en": "Scoreboard", "ru": "Скорборд"},
    "sts.toggle.keys": {"en": "Keys", "ru": "Клавиши"},
    "sts.toggle.he": {"en": "Hit error meter", "ru": "Хит-ошибки"},
    "sts.toggle.mods": {"en": "Mods", "ru": "Моды"},
    "sts.toggle.rs": {"en": "Result screen", "ru": "Экран результата"},
    "sts.toggle.sg": {"en": "Strain graph", "ru": "График сложности"},
    "sts.toggle.hc": {"en": "300/100/50 counter", "ru": "Счётчик 300/100/50"},
    "sts.toggle.sc": {"en": "Score / accuracy / grade", "ru": "Счёт / точность / грейд"},
    "sts.toggle.hp": {"en": "HP bar", "ru": "HP-бар"},
    "sts.toggle.sw": {"en": "Seizure warning", "ru": "Эпилепсия-варнинг"},
    "sts.toggle.hs": {"en": "Skin hitsounds", "ru": "Хитсаунды скина"},
    "sts.toggle.cin": {"en": "🎬 Cinema", "ru": "🎬 Кинотеатр"},
    "sts.kb.skin_label": {"en": "Skin: {skin}", "ru": "Скин: {skin}"},
    "sts.kb.my_skins": {"en": "🗂 My skins", "ru": "🗂 Мои скины"},
    "sts.kb.resolution": {"en": "Resolution: {value}", "ru": "Разрешение: {value}"},
    "sts.kb.bg_dim": {"en": "Background dim: {value}%", "ru": "Затемнение фона: {value}%"},
    "sts.kb.cursor": {"en": "Cursor: {value}x", "ru": "Курсор: {value}x"},
    "sts.kb.music_vol": {"en": "Music volume: {value}%", "ru": "Громкость музыки: {value}%"},
    "sts.kb.hitsound_vol": {"en": "Hitsound volume: {value}%", "ru": "Громкость хитсаундов: {value}%"},

    "sts.skin.header": {"en": "🎨 <b>Skin</b>\n\nCurrent: <b>{current}</b>\n", "ru": "🎨 <b>Скин</b>\n\nТекущий: <b>{current}</b>\n"},
    "sts.page_prefix": {"en": "Page {page}/{total}. ", "ru": "Стр. {page}/{total}. "},
    "sts.page_suffix": {"en": "  ({page}/{total})", "ru": "  (стр. {page}/{total})"},
    "sts.skin.pick": {"en": "Pick a skin:", "ru": "Выберите скин:"},
    "sts.kb.back_to_video": {"en": "‹ To video", "ru": "‹ К видео"},
    "sts.skin.unavailable": {"en": "Skin unavailable.", "ru": "Скин недоступен."},
    "sts.skin.selected": {"en": "Skin: {name}", "ru": "Скин: {name}"},

    "sts.myskins.header_admin": {"en": "🗂 <b>All skins (admin)</b>\n\n", "ru": "🗂 <b>Все скины (админ)</b>\n\n"},
    "sts.myskins.header": {"en": "🗂 <b>My skins</b>\n\n", "ru": "🗂 <b>Мои скины</b>\n\n"},
    "sts.myskins.empty": {
        "en": ("Skins you upload will show up here.\n"
               "Send the bot a <code>.osk</code> file, or use "
               "<code>skin &lt;link&gt;</code> for large skins."),
        "ru": ("Здесь появятся скины, загруженные вами.\n"
               "Отправьте боту файл <code>.osk</code> или используйте "
               "<code>skin &lt;ссылка&gt;</code> для больших скинов."),
    },
    "sts.total": {"en": "Total: <b>{n}</b>", "ru": "Всего: <b>{n}</b>"},
    "sts.myskins.pick": {"en": "\nPick a skin to manage:", "ru": "\nВыберите скин для управления:"},
    "sts.kb.select": {"en": "✅ Select", "ru": "✅ Выбрать"},
    "sts.kb.rename": {"en": "✏️ Rename", "ru": "✏️ Переименовать"},
    "sts.kb.delete": {"en": "🗑 Delete", "ru": "🗑 Удалить"},
    "sts.kb.back_to_list": {"en": "‹ To list", "ru": "‹ К списку"},
    "sts.myskins.detail": {
        "en": "🗂 <b>{name}</b>\n\nYour skin. What would you like to do?",
        "ru": "🗂 <b>{name}</b>\n\nВаш скин. Что сделать?",
    },
    "sts.deleting": {"en": "Deleting…", "ru": "Удаляю..."},
    "sts.skin.delete_error": {"en": "Skin deletion error: {error}", "ru": "Ошибка удаления скина: {error}"},
    "sts.skin.rename_prompt": {
        "en": "Enter a new name for skin <b>{name}</b>:",
        "ru": "Введите новое имя для скина <b>{name}</b>:",
    },
    "sts.skin.empty_name": {"en": "Name can't be empty.", "ru": "Имя не может быть пустым."},
    "sts.skin.not_yours": {"en": "This isn't your skin.", "ru": "Это не ваш скин."},
    "sts.renaming": {"en": "Renaming…", "ru": "Переименовываю..."},
    "sts.skin.rename_error": {"en": "Rename error: {error}", "ru": "Ошибка переименования: {error}"},
    "sts.skin.renamed": {"en": "Skin renamed: <b>{name}</b>", "ru": "Скин переименован: <b>{name}</b>"},

    "sts.render_reset_done": {"en": "Render settings reset ↺", "ru": "Настройки рендера сброшены ↺"},

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

    "sts.renders.header": {"en": "📼 <b>My renders</b>\n\n", "ru": "📼 <b>Мои рендеры</b>\n\n"},
    "sts.renders.empty": {
        "en": "Replays you render will show up here.\nTap 🎬 under an <code>rs</code> card.",
        "ru": "Здесь появятся отрендеренные тобой реплеи.\nЖми 🎬 под карточкой <code>rs</code>.",
    },
    "sts.renders.pick": {"en": "\nPick a replay to view:", "ru": "\nВыберите реплей для просмотра:"},
    "sts.renders.fallback_label": {"en": "Replay", "ru": "Реплей"},
    "sts.field.player": {"en": "Player", "ru": "Игрок"},
    "sts.field.mods": {"en": "Mods", "ru": "Моды"},
    "sts.field.rank": {"en": "Rank", "ru": "Ранг"},
    "sts.field.pp": {"en": "PP", "ru": "PP"},
    "sts.field.accuracy": {"en": "Accuracy", "ru": "Точность"},
    "sts.field.combo": {"en": "Combo", "ru": "Комбо"},
    "sts.field.misses": {"en": "Misses", "ru": "Промахи"},
    "sts.renders.rendered_at": {"en": "\n<i>Rendered: {date} UTC</i>", "ru": "\n<i>Отрендерено: {date} UTC</i>"},
    "sts.kb.send_video": {"en": "▶️ Send video", "ru": "▶️ Отправить видео"},
    "sts.renders.not_found": {"en": "Entry not found.", "ru": "Запись не найдена."},
    "sts.renders.broken_header": {"en": "⚠️ <b>Broken replay</b>\n\n", "ru": "⚠️ <b>Битый реплей</b>\n\n"},
    "sts.renders.broken_body": {
        "en": "The video is no longer available on Telegram (expired).\n\nDelete the entry, or try rendering it again?",
        "ru": "Видео в Telegram больше недоступно (устарело).\n\nУдалить запись или попробовать отрендерить заново?",
    },
    "sts.kb.rerender": {"en": "🔄 Re-render", "ru": "🔄 Перерендерить"},
    "sts.renders.sent": {"en": "Sent ⬆️", "ru": "Отправлено ⬆️"},
    "sts.renders.unavailable": {"en": "Replay unavailable.", "ru": "Реплей недоступен."},
    "sts.renders.deleted": {"en": "Deleted 🗑", "ru": "Удалено 🗑"},
    "sts.renders.rerender_unavailable": {
        "en": "Re-render unavailable — re-upload the .osr.",
        "ru": "Перерендер недоступен — перезалейте .osr.",
    },
    "sts.renders.rerender_missing_data": {
        "en": "Not enough data to re-render.",
        "ru": "Недостаточно данных для перерендера.",
    },
    "sts.renders.rerender_started": {"en": "Re-render started…", "ru": "Перерендер запущен..."},

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
