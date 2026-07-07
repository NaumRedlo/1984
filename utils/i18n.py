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
    "common.anon_name": {"en": "Citizen", "ru": "Гражданин"},
    "common.not_your_list": {"en": "Not your list.", "ru": "Это не ваш список."},
    "common.pages_stale": {
        "en": "Pages expired — run the command again.",
        "ru": "Страницы устарели — запросите команду снова.",
    },
    "common.group_only": {
        "en": "This command only works in a group chat.",
        "ru": "Эта команда работает только в беседе.",
    },
    "common.stale_repeat": {"en": "Expired — repeat the action.", "ru": "Устарело — повторите действие."},
    "common.something_wrong": {
        "en": "Something went wrong. Try again.",
        "ru": "Что-то пошло не так. Попробуй ещё раз.",
    },

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

    # ── render (replay -> video) ──────────────────────────────────────────
    "render.gpu_rendering": {"en": "Rendering video on GPU…", "ru": "Рендеринг видео на GPU..."},
    "gpu.starting": {"en": "Starting the GPU server (~1 min)...", "ru": "Запускаю GPU-сервер (~1 мин)..."},
    "gpu.cannot_start": {
        "en": "The GPU server can't be started — check the Intelion balance.",
        "ru": "GPU-сервер нельзя запустить — проверьте баланс Intelion.",
    },
    "gpu.start_failed": {
        "en": "Couldn't start the GPU server: {error}",
        "ru": "Не удалось запустить GPU-сервер: {error}",
    },
    "gpu.wake_timeout": {
        "en": "The GPU server didn't come up in time. Try again.",
        "ru": "GPU-сервер не успел запуститься. Попробуйте ещё раз.",
    },
    "render.loading_map": {"en": "Loading the map…", "ru": "Загрузка карты..."},
    "render.map_download_failed": {
        "en": "Couldn't download the map. Try again later.",
        "ru": "Не удалось скачать карту. Попробуйте позже.",
    },
    "render.loading_replay": {"en": "Loading the replay…", "ru": "Загрузка реплея..."},
    "render.replay_unavailable": {
        "en": ("The replay isn't available for this score.\n"
               "It may not have been saved (a fail or an old score)."),
        "ru": ("Реплей недоступен для этого скора.\n"
               "Возможно, реплей не был сохранён (фейл или старый скор)."),
    },
    "render.rendering_remote": {"en": "Rendering on the remote server…", "ru": "Рендеринг на удалённом сервере..."},
    "render.rendering_local": {"en": "Rendering video…", "ru": "Рендеринг видео..."},
    "render.queue_position": {
        "en": "Render queue position: <b>#{position}</b>. Please wait…",
        "ru": "В очереди на рендер: <b>#{position}</b>. Ожидайте...",
    },
    "render.queue_full": {
        "en": "Too many renders queued. Try again later.",
        "ru": "Слишком много рендеров в очереди. Попробуйте позже.",
    },
    "render.worker_unreachable": {
        "en": "The render server is unreachable. Try again later.",
        "ru": "Сервер рендеринга недоступен. Попробуйте позже.",
    },
    "render.render_error": {"en": "Render error: {error}", "ru": "Ошибка рендеринга: {error}"},
    "render.sending_video": {"en": "Sending video…", "ru": "Отправка видео..."},
    "render.send_failed": {
        "en": "Couldn't send the video to Telegram.",
        "ru": "Не удалось отправить видео в Telegram.",
    },
    "render.video_too_large": {
        "en": "Video is too large for Telegram ({mb} MB).",
        "ru": "Видео слишком большое для Telegram ({mb} МБ).",
    },
    "render.preparing": {
        "en": "Preparing the render for <b>{name}</b>…",
        "ru": "Подготовка рендера <b>{name}</b>...",
    },
    "render.busy": {
        "en": "Wait for the current render to finish.",
        "ru": "Дождитесь завершения текущего рендера.",
    },
    "render.cooldown_short": {"en": "Wait {sec}s.", "ru": "Подождите {sec} сек."},
    "render.started": {"en": "Render started…", "ru": "Рендер запущен..."},
    "render.kb.confirm": {"en": "🎬 Render", "ru": "🎬 Рендерить"},
    "render.kb.cancel": {"en": "Cancel", "ru": "Отмена"},
    "render.wait_before_next": {
        "en": "Wait <b>{sec}s</b> before your next render.",
        "ru": "Подождите <b>{sec} сек.</b> перед следующим рендером.",
    },
    "render.confirm_prompt": {"en": "🎬 Render this replay?", "ru": "🎬 Отрендерить этот реплей?"},
    "render.not_your_replay": {"en": "Not your replay.", "ru": "Не ваш реплей."},
    "render.file_gone": {
        "en": "The replay file is no longer available, upload it again.",
        "ru": "Файл реплея больше недоступен, загрузите заново.",
    },
    "render.searching_map_by_replay": {
        "en": "Looking up the map from the replay…",
        "ru": "Поиск карты по реплею...",
    },
    "render.osr_read_failed": {
        "en": "Couldn't read the <code>.osr</code>.",
        "ru": "Не удалось прочитать <code>.osr</code>.",
    },
    "render.std_only": {
        "en": "Only <b>osu!standard</b> replays are supported.",
        "ru": "Поддерживаются только реплеи <b>osu!standard</b>.",
    },
    "render.map_not_found": {
        "en": "This replay's map wasn't found on osu! (maybe unranked or deleted).",
        "ru": "Карта этого реплея не найдена на osu! (возможно, анранкнутая или удалённая).",
    },
    "render.danser_map_missing": {
        "en": ("Error: the map isn't in danser's database.\n"
               "Render this score via <code>rs</code> → 🎬 first so the map loads automatically."),
        "ru": ("Ошибка: карта не найдена в базе danser.\n"
               "Сначала отрендерьте этот скор через <code>rs</code> → 🎬, чтобы карта загрузилась автоматически."),
    },
    "render.generic_error": {
        "en": "An error occurred while rendering the replay.",
        "ru": "Произошла ошибка при рендере реплея.",
    },
    "render.osr_label": {"en": "Replay (.osr)", "ru": "Реплей (.osr)"},

    # ── skin (.osk install) ──────────────────────────────────────────────
    "skin.bad_link": {"en": "Invalid link.", "ru": "Некорректная ссылка."},
    "skin.bad_scheme": {
        "en": "The link must start with http:// or https://.",
        "ru": "Ссылка должна начинаться с http:// или https://.",
    },
    "skin.bad_host": {"en": "Invalid address.", "ru": "Недопустимый адрес."},
    "skin.redirect": {
        "en": "The link redirects — give a direct link to the .osk.",
        "ru": "Ссылка редиректит — дайте прямую ссылку на .osk.",
    },
    "skin.download_http_error": {"en": "Couldn't download (HTTP {status}).", "ru": "Не удалось скачать (HTTP {status})."},
    "skin.too_large": {"en": "File is too large (> {mb} MB).", "ru": "Файл слишком большой (> {mb} МБ)."},
    "skin.download_failed": {"en": "Couldn't download from the link.", "ru": "Не удалось скачать по ссылке."},
    "skin.not_osk": {"en": "This doesn't look like a .osk file (zip).", "ru": "Это не похоже на файл .osk (zip)."},
    "skin.remote_only": {
        "en": "Skin uploads are only available in remote render mode.",
        "ru": "Загрузка скинов доступна только в режиме удалённого рендера.",
    },
    "skin.cooldown": {
        "en": "Wait <b>{sec}s</b> before uploading the next skin.",
        "ru": "Подождите <b>{sec} сек.</b> перед загрузкой следующего скина.",
    },
    "skin.uploading": {"en": "Uploading the skin to the server…", "ru": "Загрузка скина на сервер..."},
    "skin.install_error": {"en": "Skin install error: {error}", "ru": "Ошибка установки скина: {error}"},
    "skin.install_failed": {"en": "Error installing the skin.", "ru": "Ошибка при установке скина."},
    "skin.installed": {
        "en": "Skin installed: <b>{name}</b>\nSelect it in <code>sts</code> → 🎨 Video.",
        "ru": "Скин установлен: <b>{name}</b>\nВыберите его в <code>sts</code> → 🎨 Видео.",
    },
    "skin.tg_too_large": {
        "en": ("File is too large for Telegram (> {mb} MB).\n"
               "Send a link instead: <code>skin &lt;direct link to .osk&gt;</code>"),
        "ru": ("Файл слишком большой для Telegram (> {mb} МБ).\n"
               "Пришлите ссылку: <code>skin &lt;прямая ссылка на .osk&gt;</code>"),
    },
    "skin.tg_download_failed": {
        "en": ("Couldn't download the file from Telegram. If it's large, send a "
               "link instead: <code>skin &lt;link to .osk&gt;</code>"),
        "ru": ("Не удалось скачать файл из Telegram. Если он большой — пришлите "
               "ссылку: <code>skin &lt;ссылка на .osk&gt;</code>"),
    },
    "skin.usage": {
        "en": ("Usage: <code>skin &lt;direct link to .osk&gt; [name]</code>\n"
               "For large skins (Telegram doesn't accept files > 20 MB)."),
        "ru": ("Использование: <code>skin &lt;прямая ссылка на .osk&gt; [название]</code>\n"
               "Для больших скинов (Telegram не принимает файлы > 20 МБ)."),
    },
    "skin.downloading": {"en": "Downloading the skin from the link…", "ru": "Скачиваю скин по ссылке..."},

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

    # ── lb / lbm (leaderboard) ───────────────────────────────────────────
    "lb.cat.pp": {"en": "PP/Rank", "ru": "PP/Ранг"},
    "lb.cat.accuracy": {"en": "Accuracy", "ru": "Точность"},
    "lb.cat.play_count": {"en": "Playcount", "ru": "Плейкаунт"},
    "lb.cat.play_time": {"en": "Time", "ru": "Время"},
    "lb.cat.ranked_score": {"en": "R. Score", "ru": "Р. очки"},
    "lb.cat.hits_per_play": {"en": "HPP", "ru": "ХПП"},
    "lb.cat.best_pp": {"en": "Top Score", "ru": "Топ скор"},
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

    # ── tt (titles collection) / st (set title) ──────────────────────────
    "tt.load_error": {
        "en": "An error occurred while loading the title collection.",
        "ru": "Произошла ошибка при загрузке коллекции титулов.",
    },
    "tt.not_your_collection": {"en": "Not your collection.", "ru": "Не ваша коллекция."},
    "tt.stale": {
        "en": "Expired — run titles again.",
        "ru": "Устарело — запустите titles снова.",
    },
    "st.usage": {
        "en": "Usage: <code>st &lt;name&gt;</code> or <code>st off</code>.",
        "ru": "Использование: <code>st &lt;имя&gt;</code> или <code>st off</code>.",
    },
    "st.cleared": {"en": "Title cleared.", "ru": "Титул снят."},
    "st.not_found": {
        "en": "No unlocked title matches “{query}”.",
        "ru": "Нет открытого титула по запросу «{query}».",
    },
    "st.ambiguous": {
        "en": "Ambiguous — several match: {names}.",
        "ru": "Уточни — подходит несколько: {names}.",
    },
    "st.set": {
        "en": "★ Active title: <b>{name}</b> ({rarity}). Shown in pf.",
        "ru": "★ Активный титул: <b>{name}</b> ({rarity}). Виден в pf.",
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
