"""requests (map challenges) message/button strings for the i18n catalog.

Merged into utils.i18n._CATALOG at import; see utils/i18n/__init__.py.
"""

CATALOG = {
    # ── req: condition summary fragments ──────────────────────────────────
    "req.cond.pass": {"en": "pass", "ru": "пройти"},
    "req.cond.play": {"en": "any play", "ru": "любой плей"},
    "req.cond.acc": {"en": "acc ≥ {value}%", "ru": "acc ≥ {value}%"},
    "req.cond.fc": {"en": "FC", "ru": "FC"},
    "req.cond.combo": {"en": "combo ≥ {value}", "ru": "комбо ≥ {value}"},
    "req.cond.mods": {"en": "mods {value}", "ru": "моды {value}"},
    "req.cond.rank": {"en": "rank ≥ {value}", "ru": "ранг ≥ {value}"},
    "req.val.off": {"en": "—", "ru": "—"},

    # ── req: wizard (req) ─────────────────────────────────────────────────
    "req.not_registered": {
        "en": "You're not registered in this group. Use <code>reg</code> first.",
        "ru": "Вы не зарегистрированы в этой группе. Сначала <code>reg</code>.",
    },
    "req.wizard.ask_target": {
        "en": "Who do you challenge? Reply to their message, or send their osu! username.",
        "ru": "Кому отправить реквест? Ответьте (reply) на его сообщение или пришлите ник osu!.",
    },
    "req.wizard.ask_target_dm": {
        "en": "Who do you challenge? Send their osu! username (they must be registered in your selected group).",
        "ru": "Кому отправить реквест? Пришлите ник osu! (игрок должен быть зарегистрирован в выбранной группе).",
    },
    "req.wizard.target_not_found": {
        "en": "No registered player found for that. Try a reply or an exact osu! username.",
        "ru": "Зарегистрированный игрок не найден. Попробуйте reply или точный ник osu!.",
    },
    "req.wizard.target_self": {
        "en": "You can't send a request to yourself.",
        "ru": "Нельзя отправить реквест самому себе.",
    },
    "req.wizard.ask_map": {
        "en": "Target: <b>{target}</b>. Now send the beatmap link or id.",
        "ru": "Адресат: <b>{target}</b>. Теперь пришлите ссылку на карту или её id.",
    },
    "req.wizard.map_not_found": {
        "en": "Couldn't resolve that beatmap. Send a valid osu! beatmap link or id.",
        "ru": "Не удалось найти карту. Пришлите корректную ссылку osu! или id.",
    },
    "req.wizard.dup": {
        "en": "You already have an active request for that player on this map.",
        "ru": "У вас уже есть активный реквест этому игроку на эту карту.",
    },
    "req.wizard.menu": {
        "en": "<b>New request</b> <i>(BETA)</i>\nTo: <b>{target}</b>\nMap: <b>{map}</b>\nConditions: {conditions}",
        "ru": "<b>Новый реквест</b> <i>(BETA)</i>\nКому: <b>{target}</b>\nКарта: <b>{map}</b>\nУсловия: {conditions}",
    },
    "req.wizard.sent": {
        "en": "📨 Request sent to <b>{target}</b>.",
        "ru": "📨 Реквест отправлен игроку <b>{target}</b>.",
    },
    "req.wizard.cancelled": {"en": "Cancelled.", "ru": "Отменено."},

    # ── req: condition menu buttons ───────────────────────────────────────
    "req.kb.pass": {"en": "Require pass: {mark}", "ru": "Требовать pass: {mark}"},
    "req.kb.acc": {"en": "Min acc: {value}", "ru": "Мин. acc: {value}"},
    "req.kb.combo": {"en": "Combo: {value}", "ru": "Комбо: {value}"},
    "req.kb.mods": {"en": "Mods: {value}", "ru": "Моды: {value}"},
    "req.kb.rank": {"en": "Min rank: {value}", "ru": "Мин. ранг: {value}"},
    "req.kb.edit": {"en": "✏️", "ru": "✏️"},
    "req.kb.send": {"en": "📨 Send", "ru": "📨 Отправить"},
    "req.kb.cancel": {"en": "✖️ Cancel", "ru": "✖️ Отмена"},

    # ── req: custom value input ───────────────────────────────────────────
    "req.custom.acc": {
        "en": "Send the minimum accuracy as a number, 0–100 (e.g. 96).",
        "ru": "Пришлите минимальную точность числом, 0–100 (например, 96).",
    },
    "req.custom.combo": {
        "en": "Send the minimum combo as a whole number (e.g. 850).",
        "ru": "Пришлите минимальное комбо целым числом (например, 850).",
    },
    "req.custom.mods": {
        "en": "Send the required mods (e.g. HDDT), or - for no mods.",
        "ru": "Пришлите нужные моды (например, HDDT), или - для «без модов».",
    },
    "req.custom.bad_number": {
        "en": "Send a whole number.", "ru": "Пришлите целое число.",
    },
    "req.custom.bad_acc": {
        "en": "Accuracy must be between 0 and 100.",
        "ru": "Точность должна быть от 0 до 100.",
    },

    # ── req: notifications + accept/decline ──────────────────────────────
    "req.notify.new": {
        "en": "🎯 <i>(BETA)</i> {target}, <b>{sender}</b> challenges you:\n<b>{map}</b>\nConditions: {conditions}",
        "ru": "🎯 <i>(BETA)</i> {target}, <b>{sender}</b> бросает вам вызов:\n<b>{map}</b>\nУсловия: {conditions}",
    },
    "req.notify.note": {"en": "\n💬 {note}", "ru": "\n💬 {note}"},
    "req.notify.completed": {
        "en": "✅ {target} completed <b>{sender}</b>'s challenge:\n<b>{map}</b>",
        "ru": "✅ {target} выполнил вызов от <b>{sender}</b>:\n<b>{map}</b>",
    },
    "req.kb.accept": {"en": "✅ Accept", "ru": "✅ Принять"},
    "req.kb.decline": {"en": "❌ Decline", "ru": "❌ Отклонить"},
    "req.accepted_alert": {"en": "Accepted — good luck!", "ru": "Принято — удачи!"},
    "req.declined_alert": {"en": "Declined.", "ru": "Отклонено."},
    "req.not_your_request": {
        "en": "This request isn't addressed to you.",
        "ru": "Этот реквест адресован не вам.",
    },
    "req.request_gone": {
        "en": "This request is no longer available.",
        "ru": "Этот реквест больше недоступен.",
    },
    "req.already_answered": {
        "en": "This request was already answered.",
        "ru": "На этот реквест уже ответили.",
    },

    # ── req: hub (reqs) ───────────────────────────────────────────────────
    "req.hub.title": {"en": "<b>🎯 Requests</b> <i>(BETA)</i>", "ru": "<b>🎯 Реквесты</b> <i>(BETA)</i>"},
    "req.kb.inbox": {"en": "📥 Incoming ({n})", "ru": "📥 Входящие ({n})"},
    "req.kb.tasks": {"en": "🎯 My tasks ({n})", "ru": "🎯 Мои задания ({n})"},
    "req.kb.sent": {"en": "📤 Sent ({n})", "ru": "📤 Отправленные ({n})"},
    "req.kb.close": {"en": "✖️ Close", "ru": "✖️ Закрыть"},
    "req.kb.back": {"en": "‹ Back", "ru": "‹ Назад"},
    "req.hub.inbox_title": {"en": "<b>📥 Incoming requests</b>", "ru": "<b>📥 Входящие реквесты</b>"},
    "req.hub.inbox_empty": {"en": "\nNothing incoming.", "ru": "\nНет входящих."},
    "req.hub.tasks_title": {"en": "<b>🎯 My tasks</b>", "ru": "<b>🎯 Мои задания</b>"},
    "req.hub.tasks_empty": {"en": "\nNo active tasks.", "ru": "\nНет активных заданий."},
    "req.hub.sent_title": {"en": "<b>📤 Sent requests</b>", "ru": "<b>📤 Отправленные реквесты</b>"},
    "req.hub.sent_empty": {"en": "\nYou haven't sent any.", "ru": "\nВы ничего не отправляли."},

    "req.inbox.item": {
        "en": "\n\n<b>{map}</b>\nfrom {sender} · {conditions}",
        "ru": "\n\n<b>{map}</b>\nот {sender} · {conditions}",
    },
    "req.task.item": {
        "en": "\n\n<b>{map}</b>\n{conditions}\n{progress}",
        "ru": "\n\n<b>{map}</b>\n{conditions}\n{progress}",
    },
    "req.task.progress": {
        "en": "best {pct}% · {attempts} attempts",
        "ru": "лучший {pct}% · попыток: {attempts}",
    },
    "req.task.no_attempts": {
        "en": "no attempts yet",
        "ru": "попыток пока нет",
    },
    "req.task.fails": {
        "en": " · fails mostly at {bucket}%",
        "ru": " · чаще падает на {bucket}%",
    },
    "req.sent.item": {
        "en": "\n\n<b>{map}</b>\nto {target} · {status}",
        "ru": "\n\n<b>{map}</b>\nкому {target} · {status}",
    },
    "req.kb.accept_n": {"en": "✅ Accept #{n}", "ru": "✅ Принять #{n}"},
    "req.kb.decline_n": {"en": "❌ Decline #{n}", "ru": "❌ Отклонить #{n}"},
    "req.kb.cancel_task_n": {"en": "🚫 Drop #{n}", "ru": "🚫 Снять #{n}"},
    "req.cancelled_alert": {"en": "Task cancelled.", "ru": "Задание снято."},

    # request status labels (sent list)
    "req.status.pending": {"en": "pending", "ru": "ожидает"},
    "req.status.accepted": {"en": "accepted", "ru": "принят"},
    "req.status.declined": {"en": "declined", "ru": "отклонён"},
    "req.status.completed": {"en": "completed ✅", "ru": "выполнен ✅"},
    "req.status.cancelled": {"en": "cancelled", "ru": "снят"},
}
