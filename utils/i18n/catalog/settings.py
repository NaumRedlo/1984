"""settings message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
    # ── sts (settings menu) ────────────────────────────────────────────────
    "sts.foreign_menu": {
        "en": "This isn't your menu. Open your own: sts",
        "ru": "Это не ваше меню. Откройте своё: sts",
    },
    "sts.home": {"en": "<b>Settings</b>\n\nPick a section:", "ru": "<b>Настройки</b>\n\nВыберите нужный раздел:"},
    "sts.kb.account": {"en": "Account", "ru": "Аккаунт"},
    "sts.kb.title": {"en": "Titles", "ru": "Титулы"},
    "sts.kb.language": {"en": "Language", "ru": "Язык"},
    "sts.kb.render": {"en": "Render", "ru": "Рендер"},

    # ── render section ───────────────────────────────────────────────────
    "sts.rnd.body": {
        "en": "<b>Render</b>\n{summary}",
        "ru": "<b>Рендер</b>\n{summary}",
    },
    "sts.rnd.ration": {
        "en": "Ultra-high resolution: {left} of {total} left today.",
        "ru": "Сверхвысокое разрешение: осталось {left} из {total} на сегодня.",
    },
    "sts.rnd.ration_needs_account": {
        "en": "Link your account to access ultra-high resolution.",
        "ru": "Привяжи свой аккаунт, чтобы использовать сверхвысокое разрешение.",
    },
    "sts.rnd.ration_spent": {
        "en": "The limited ultra-high-resolution quota has been used up. Use any resolution up to and including 1080p and 60 FPS.",
        "ru": "Ограниченный лимит сверхвысокого разрешения израсходован. Используй любое разрешение до 1080р и 60 FPS включительно.",
    },
    "sts.rnd.mute": {"en": "Muted", "ru": "Без звука"},
    "sts.rnd.background": {"en": "Map background", "ru": "Фон карты"},
    "sts.rnd.bare": {"en": "No interface", "ru": "Без интерфейса"},
    "sts.rnd.leaderboard": {"en": "Scoreboard", "ru": "Скорборд"},
    "sts.rnd.no_board": {"en": "No scoreboard", "ru": "Без скорборда"},
    "sts.rnd.map_hitsounds": {
        "en": "Map's hitsounds",
        "ru": "Хитсаунды карты",
    },
    "sts.rnd.sound_on": {"en": "with sound", "ru": "со звуком"},
    "sts.rnd.sound_mix": {
        "en": "music {music}% · hits {hits}%",
        "ru": "музыка {music}% · хиты {hits}%",
    },
    "sts.rnd.sound_off": {"en": "muted", "ru": "без звука"},
    "sts.rnd.unknown": {"en": "No such setting.", "ru": "Такой настройки нет."},
    "sts.rnd.skin": {"en": "Skin — send an .osk to add one:",
                     "ru": "Скин — пришли .osk, чтобы добавить:"},
    "sts.rnd.skin_default": {"en": "Dossier Default", "ru": "Dossier Default"},
    "sts.rnd.skin_gone": {
        "en": "This skin is no longer available. Please send it again.",
        "ru": "Этого скина больше нет. Пришли его заново.",
    },
    "sts.fx.body": {
        "en": "<b>Gameplay</b>",
        "ru": "<b>Геймплей</b>",
    },
    "sts.fx.tab": {"en": "Gameplay", "ru": "Геймплей"},
    "sts.fx.snake-in": {
        "en": "Snaking in sliders",
        "ru": "Выдвигающиеся слайдеры",
    },
    "sts.fx.snake-out": {
        "en": "Snaking out sliders",
        "ru": "Задвигающиеся слайдеры",
    },
    "sts.fx.cursor-expand": {
        "en": "Cursor expanding",
        "ru": "Нажатие курсора",
    },
    "sts.fx.cursor-trail": {"en": "Cursor trail", "ru": "След за курсором"},
    "sts.fx.keypad": {"en": "Key overlay", "ru": "Кейпад"},
    "sts.fx.key-bars": {
        "en": "Key bars",
        "ru": "Полосы нажатия",
    },
    "sts.fx.unstable-rate": {
        "en": "Unstable Rate",
        "ru": "UR над шкалой попаданий",
    },
    "sts.fx.slider-ball-tint": {
        "en": "Slider ball in combo colour",
        "ru": "Слайдербол в цвете комбо",
    },
    "sts.fx.hit-lighting": {
        "en": "Hit lighting",
        "ru": "Вспышка от попадания",
    },
    "sts.qly.dim": {"en": "Background dim", "ru": "Затемнение фона"},
    "sts.qly.meter": {"en": "Hit-error meter", "ru": "Шкала точности"},
    "sts.snd.volume": {"en": "Overall volume", "ru": "Общая громкость"},
    "sts.skn.tab": {"en": "Skin", "ru": "Скин"},
    "sts.skn.mine": {"en": "— Yours —", "ru": "— Загруженные —"},
    "sts.skn.shared": {"en": "— Shared —", "ru": "— Общие —"},
    "sts.skn.none_yours": {
        "en": "nothing yet — send an .osk",
        "ru": "пока ничего — пришлите .osk",
    },
    "sts.skn.body": {
        "en": "<b>Skin</b>\nSend an <code>.osk</code> to add your own.",
        "ru": "<b>Скин</b>\nЧтобы добавить свой — пришлите <code>.osk</code>.",
    },
    "sts.qly.size": {"en": "Size", "ru": "Размер"},
    "sts.qly.fps": {"en": "Frame rate", "ru": "Кадры"},
    "sts.typed.ask": {
        "en": "<b>{name}</b>\nReply with a value. {hint}",
        "ru": "<b>{name}</b>\nОтветьте значением. {hint}",
    },
    "sts.typed.no": {
        "en": "Not a value I can use. {hint}",
        "ru": "Такое значение не подходит. {hint}",
    },
    "sts.typed.set": {"en": "{name} — {value}", "ru": "{name} — {value}"},
    "sts.typed.as_it_comes": {"en": "as it comes", "ru": "как есть"},
    "sts.typed.size_hint": {
        "en": "Width×height, both even, 256 to 3840. Example: 1600x900",
        "ru": "Ширина×высота, обе чётные, от 256 до 3840. Например: 1600x900",
    },
    "sts.typed.fps_hint": {"en": "15 to 240.", "ru": "От 15 до 240."},
    "sts.typed.percent_hint": {"en": "0 to 100.", "ru": "От 0 до 100."},
    "sts.typed.meter_hint": {"en": "25 to 300 per cent.", "ru": "От 25 до 300 процентов."},
    "sts.typed.volume_hint": {"en": "0 to 200 per cent.", "ru": "От 0 до 200 процентов."},
    "sts.qly.tab": {"en": "Quality", "ru": "Качество"},
    "sts.qly.body": {
        "en": "<b>Quality customization</b>\nHow big and how smooth: {summary}",
        "ru": "<b>Настройка качества</b>\nНасколько крупно и насколько плавно: {summary}",
    },
    "sts.snd.tab": {"en": "Sound", "ru": "Звук"},
    "sts.snd.body": {
        "en": "<b>Sound</b>",
        "ru": "<b>Звук</b>",
    },
    "sts.snd.music": {"en": "Music", "ru": "Музыка"},
    "sts.snd.hitsounds": {"en": "Hit sounds", "ru": "Хитсаунды"},
    "sts.snd.muted": {
        "en": "The render is muted: untick <i>Muted</i> below.",
        "ru": "Рендер без звука: снимите <i>Без звука</i> ниже.",
    },
    "sts.fx.now_on": {"en": "{name} — on", "ru": "{name} — включено"},
    "sts.fx.now_off": {"en": "{name} — off", "ru": "{name} — выключено"},
    "sts.fx.back": {"en": "← Render", "ru": "← Рендер"},

    "sts.rnd.share": {
        "en": "Send replay data to the developer",
        "ru": "Отправлять данные реплея разработчику",
    },
    "sts.rnd.share_on": {
        "en": ("Every replay you render is sent to the bot's author (the .osr file itself and what the engine made of it). Used to find where the engine judges a play wrongly. Turn it off at any time; it changes nothing else."),
        "ru": ("Каждый отрендеренный реплей уходит автору бота (сам файл .osr и то, что о нём сказал движок). Нужно, чтобы находить места, где движок судит неверно. Выключить можно в любой момент, на остальное это не влияет."),
    },
    "sts.rnd.share_agreed": {
        "en": "Your replays and the engine's reading of them are sent to the bot's developer. Turn it off here at any time.",
        "ru": "Твои реплеи и разбор движка уходят разработчику бота. Выключить можно здесь в любой момент.",
    },
    "sts.rnd.share_off": {
        "en": "Nothing is sent...",
        "ru": "Ничего не отправляется...",
    },
    "sts.rnd.share_needs_account": {
        "en": "Link your osu! account to the bot to send data about your rendered replays.",
        "ru": "Привяжи свой osu! аккаунт к боту для отправки данных об отрендеренных реплеях.",
    },
    "sts.kb.close": {"en": "Close", "ru": "Закрыть"},
    "sts.kb.back": {"en": "‹ Back", "ru": "‹ Назад"},
    "sts.not_registered": {"en": "You aren't registered. Write register [name]", "ru": "Вы не зарегистрированы. Напишите register [ник]"},



    "sts.page_suffix": {"en": "  ({page}/{total})", "ru": "  (стр. {page}/{total})"},



    "sts.acc.not_linked": {
        "en": "👤 <b>The osu! account</b>\n\nisn't linked to a bot.\nJoin a chat that has a bot: <code>register [name]</code>",
        "ru": "👤 <b>Аккаунт</b>\n\nosu! не привязан к боту.\nЗарегистрируйтесь в беседе, где есть бот: <code>register [ник]</code>",
    },
    "sts.acc.linked": {
        "en": "👤 <b>Account</b>\n\nosu!: <b>{name}</b>\nOAuth-authorization: {status}",
        "ru": "👤 <b>Аккаунт</b>\n\nosu!: <b>{name}</b>\nOAuth-авторизация: {status}",
    },
    "sts.acc.oauth_yes": {"en": "✅ linked", "ru": "✅ привязан"},
    "sts.acc.oauth_no": {"en": "❌ not linked", "ru": "❌ не привязан"},
    "sts.kb.relink": {"en": "🔁 Re-link osu! account", "ru": "🔁 Перепривязать osu! аккаунт"},
    "sts.kb.link": {"en": "🔗 Link osu!", "ru": "🔗 Привязать osu!"},
    "sts.kb.unlink": {"en": "❌ Unlink osu! account", "ru": "❌ Отвязать osu! аккаунт"},
    "sts.acc.relink_title": {"en": "🔁 Re-linking osu! account", "ru": "🔁 Перепривязка osu! аккаунта"},
    "sts.acc.link_title": {"en": "🔗 Linking osu! account", "ru": "🔗 Привязка osu! аккаунта"},
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
    "sts.acc.not_linked_alert": {"en": "osu! account isn't linked.", "ru": "osu! аккаунт не привязан."},
    "sts.acc.unlink_cooldown": {
        "en": "Unlinking is available once a month. Try again in {remaining}.",
        "ru": "Отвязка доступна раз в месяц. Повторите через {remaining}.",
    },
    "sts.acc.unlinked": {
        "en": "✅ osu! account unlinked. You can unlink again in a month.",
        "ru": "✅ Аккаунт osu! отвязан. Повторная отвязка доступна через месяц.",
    },
    "sts.done": {"en": "Done", "ru": "Готово"},

    "sts.lang.view": {
        "en": "<b>Language</b>\n\nCurrent: <b>{current}</b>\nApplies to the entire text.",
        "ru": "<b>Язык</b>\n\nТекущий: <b>{current}</b>\nВлияет на весь текст.",
    },
    "sts.lang.set_alert": {"en": "Language: {lang}", "ru": "Язык: {lang}"},

    "sts.title.header": {"en": "<b>Titles</b>\n\nActive: <b>{name}</b>\n\n", "ru": "<b>Титулы</b>\n\nАктивный: <b>{name}</b>\n\n"},
    "sts.title.none": {"en": "— none —", "ru": "— нет —"},
    "sts.title.no_unlocked": {
        "en": "No unlocked titles yet. Unlock them by playing — <code>tt</code>.",
        "ru": "Пока нет открытых титулов. Открывайте их во время иры — <code>tt</code>.",
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
        "en": "Using data from <b>{label}</b>.\nYou can change it using the command <code>group</code>.",
        "ru": "Использую данные беседы <b>{label}</b>.\nМожно сменить при помощи команды <code>group</code>.",
    },
    "dm.pick_group": {
        "en": "In which conversation should you display your information? Pick one:",
        "ru": "В какой беседе показывать ваши данные? Выберите одну:",
    },
    "dm.pick_first": {"en": "Pick a group first.", "ru": "Сначала выберите беседу."},
    "dm.bad_choice": {"en": "Invalid choice.", "ru": "Некорректный выбор."},
    "dm.group_unavailable": {"en": "That group isn't available.", "ru": "Эта беседа недоступна."},
    "dm.done": {"en": "Done!", "ru": "Готово!"},
    "dm.switched": {
        "en": ("Using data from <b>{label}</b>.\n"
               "You can change it using the command <code>group</code>.\n"
               "Now repeat your command."),
        "ru": ("Использую данные беседы <b>{label}</b>.\n"
               "Можно сменить при помощи команды <code>group</code>.\n"
               "Теперь повторите свою команду."),
    },
}
