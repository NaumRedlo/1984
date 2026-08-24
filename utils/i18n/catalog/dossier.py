"""What the engine says while it reads, judges and renders a replay.

Every line somebody sees between sending an `.osr` and getting a video back.
It was written in Russian and stayed that way through the whole flow, which is
the one part of the bot a person cannot avoid — the settings can be left alone
and a card can be glanced at, but nobody gets a render without reading these.

The engine's own read-outs are a different surface and are not here. Those are
the four buttons under a finished render, they quote figures rather than
sentences, and they are read by somebody checking the engine rather than by
somebody using it.
"""

CATALOG = {
    # ── reading the file ─────────────────────────────────────────────────
    "dsr.not_built": {
        "en": "Dossier: the engine is not built.\n",
        "ru": "Dossier: движок не собран.\n",
    },
    "dsr.ready": {
        "en": "Dossier is ready. Send an <code>.osr</code> as a file — I will judge "
              "it and compare with the replay's own header.",
        "ru": "Dossier готов. Пришли <code>.osr</code> файлом — прогоню судейство "
              "и сверю с заголовком реплея.",
    },
    "dsr.too_big": {
        "en": "That does not look like a replay — the file is too large.",
        "ru": "Это не похоже на реплей — слишком большой файл.",
    },
    "dsr.reading": {"en": "Reading the replay…", "ru": "Читаю реплей…"},
    "dsr.download_failed": {
        "en": "Could not download the file: {why}",
        "ru": "Не удалось скачать файл: {why}",
    },
    "dsr.unreadable": {
        "en": "The replay did not parse: {why}",
        "ru": "Реплей не разобрался: {why}",
    },
    "dsr.wrong_mode": {
        "en": "osu!standard only for now, and this is {mode}.",
        "ru": "Пока только osu!standard, а тут {mode}.",
    },
    "dsr.no_frames": {
        "en": "The replay has no frames — there is nothing to judge.",
        "ru": "В реплее нет кадров — судить нечего.",
    },
    "dsr.finding_map": {
        "en": "{player} · {mods} · {frames} frames. Looking for the map…",
        "ru": "{player} · {mods} · {frames} кадров. Ищу карту…",
    },
    "dsr.judging": {"en": "{map}\nJudging…", "ru": "{map}\nСужу…"},
    "dsr.judge_failed": {
        "en": "The judging did not happen: {why}",
        "ru": "Судейство не состоялось: {why}",
    },

    # ── a skin somebody sent ─────────────────────────────────────────────
    "dsr.skin_too_big_telegram": {
        "en": "That skin is over {mb} MB — Telegram will not hand us more than "
              "that. It is the cloud Bot API's limit, not ours.",
        "ru": "Скин больше {mb} МБ — столько Telegram нам не отдаёт. "
              "Это предел облачного Bot API, а не наш.",
    },
    "dsr.skin_too_big": {
        "en": "That skin is over {mb} MB — more than we take.",
        "ru": "Скин больше {mb} МБ — столько мы не берём.",
    },
    "dsr.skin_taking": {"en": "Taking the skin…", "ru": "Забираю скин…"},
    "dsr.skin_refused": {
        "en": "Skin not accepted: {why}",
        "ru": "Скин не принят: {why}",
    },
    "dsr.skin_stored": {
        "en": "Skin <b>{name}</b> stored — {count} {word}.\n"
              "To render in it: <code>sts</code> → Render.",
        "ru": "Скин <b>{name}</b> сохранён — {count} {word}.\n"
              "Выбрать его для рендера: <code>sts</code> → Рендер.",
    },

    # ── setting a render going ───────────────────────────────────────────
    "dsr.gone": {
        "en": "That replay is no longer held — send it again.",
        "ru": "Реплей уже не хранится — пришли его заново.",
    },
    "dsr.busy": {
        "en": "Already rendering another replay — give it a moment.",
        "ru": "Уже рендерю другой реплей, подожди.",
    },
    "dsr.ration_spent": {
        "en": "No renders above 1080p60 left today ({total} a day). Pick a "
              "smaller size in /sts.",
        "ru": "На сегодня рендеры выше 1080p60 закончились "
              "({total} в день). Выбери размер ниже в /sts.",
    },
    "dsr.board_building": {
        "en": "Building the chat's scoreboard…",
        "ru": "Собираю скорборд беседы…",
    },
    "dsr.board_progress": {
        "en": "Building the chat's scoreboard… {done}/{total}",
        "ru": "Собираю скорборд беседы… {done}/{total}",
    },
    "dsr.no_audio": {
        "en": "\n⚠️ The map's archive came from no mirror — the map was taken "
              "from osu! directly, so the video will have no music.",
        "ru": "\n⚠️ Архив карты не достался ни с одного зеркала — карта взята напрямую "
              "у osu!, так что видео выйдет без музыки.",
    },
    "dsr.rendering": {
        "en": "Rendering {what}… this takes minutes.",
        "ru": "Рендерю {what}… это займёт минуты.",
    },
    "dsr.reel_failed": {
        "en": "The reel could not be chosen.\n<pre>{why}</pre>",
        "ru": "Экспозитор не справился.\n<pre>{why}</pre>",
    },
    "dsr.reel_chose": {
        "en": "The reel picked {found} {word} — {seconds:.0f}s. Rendering… "
              "this takes minutes.",
        "ru": "Экспозитор выбрал {found} {word} — "
              "{seconds:.0f} с. Рендерю… это займёт минуты.",
    },

    # ── while it runs, and how it ends ───────────────────────────────────
    "dsr.progress": {
        "en": "Rendering {size}{which}\n"
              "{done}/{total} frames · {fps:.0f}/s · about {left} left",
        "ru": "Рендерю {size}{which}\n"
              "{done}/{total} кадров · {fps:.0f}/с · осталось ~{left}",
    },
    "dsr.progress_clip": {
        "en": " · clip {at}/{of}",
        "ru": " · клип {at}/{of}",
    },
    "dsr.cancelled": {"en": "Render cancelled.", "ru": "Рендер отменён."},
    "dsr.failed": {
        "en": "The render failed.\n<pre>{why}</pre>",
        "ru": "Рендер не удался.\n<pre>{why}</pre>",
    },
    "dsr.too_big_to_send": {
        "en": "Done, but the file is {mb:.0f} MB — more than this Bot API "
              "accepts.\nIt is on the host: <code>{path}</code>",
        "ru": "Готово, но файл {mb:.0f} МБ — больше, чем этот Bot API принимает.\n"
              "Лежит на хосте: <code>{path}</code>",
    },
    "dsr.sending": {
        "en": "Done — {mb:.1f} MB, {size}. Sending…",
        "ru": "Готово — {mb:.1f} МБ, {size}. Отправляю…",
    },
    "dsr.send_failed": {
        "en": "Rendered ({mb:.1f} MB) but could not send it: {why}\n"
              "The file is on the host: <code>{path}</code>",
        "ru": "Отрендерил ({mb:.1f} МБ), но отправить не вышло: {why}\n"
              "Файл на хосте: <code>{path}</code>",
    },
    "dsr.sent": {
        "en": "Sent — {mb:.1f} MB, {width}×{height}, {seconds}s.",
        "ru": "Отправлено — {mb:.1f} МБ, {width}×{height}, {seconds} с.",
    },

    # ── the cancel button ────────────────────────────────────────────────
    "dsr.cancel": {"en": "✖️ Cancel", "ru": "✖️ Отменить"},
    "dsr.nothing_to_cancel": {
        "en": "Nothing left to cancel.",
        "ru": "Уже нечего отменять.",
    },
    "dsr.cancelling": {"en": "Cancelling…", "ru": "Отменяю…"},
    "dsr.again": {"en": "🎬 Again", "ru": "🎬 Ещё раз"},
    "dsr.summary": {"en": "📋 Render report", "ru": "📋 Итоги рендера"},
    "dsr.summary_gone": {
        "en": "The report is gone — the replay has left memory.",
        "ru": "Итогов уже нет — реплей выселен из памяти.",
    },
    "dsr.engine_said_nothing": {
        "en": "(the engine said nothing)",
        "ru": "(движок ничего не сообщил)",
    },

    # ── the buttons under a card ─────────────────────────────────────────
    "dsr.kb.render": {"en": "🎬 Render", "ru": "🎬 Отрендерить"},
    "dsr.kb.reel": {"en": "✂️ Reel", "ru": "✂️ Экспозитор"},
    "dsr.kb.map": {"en": "🗺 Map", "ru": "🗺 Карта"},
    "dsr.kb.board": {"en": "🏆 Map top", "ru": "🏆 Топ карты"},

    # ── how long is left ─────────────────────────────────────────────────
    "dsr.about_a_minute": {"en": "~1 min", "ru": "~1 мин"},
    "dsr.almost_done": {"en": "~0 min", "ru": "~0 мин"},
    "dsr.seconds": {"en": "{seconds}s", "ru": "{seconds} с"},
    "dsr.minutes": {
        "en": "{minutes} min {seconds:02d}s",
        "ru": "{minutes} мин {seconds:02d} с",
    },

    # ── why there is no scoreboard ───────────────────────────────────────
    "dsr.board_dm": {
        "en": "No scoreboard: in a private chat the bot does not know whose "
              "chat to compare. Send the replay to a chat, or pick one for DMs.",
        "ru": "Скорборда нет: в личке бот не знает, чью беседу сравнивать. "
              "Пришли реплей в беседу или выбери её для лички.",
    },
    "dsr.board_status": {
        "en": "No scoreboard: the map is {status}, and osu! keeps no table for "
              "those.",
        "ru": "Скорборда нет: у карты статус {status}, у osu! на такие нет таблицы.",
    },
    "dsr.board_stranger": {
        "en": "No scoreboard: {player} is not in this chat, so there is nobody "
              "to compare a stranger's run against.",
        "ru": "Скорборда нет: {player} нет в беседе, "
              "а сравнивать чужой прогон не с кем.",
    },
    "dsr.board_that_player": {"en": "this player", "ru": "этого игрока"},
    "dsr.board_empty": {
        "en": "No scoreboard: nobody in this chat has a score on this map.",
        "ru": "Скорборда нет: ни у кого из беседы нет счёта на этой карте.",
    },
}
