"""render message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
    # ── render (replay -> video) ──────────────────────────────────────────
    "render.gpu_rendering": {"en": "Rendering video on GPU…", "ru": "Рендеринг видео на GPU..."},
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
}
