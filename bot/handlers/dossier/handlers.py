"""Throwing a real replay at the engine.

Send the bot an `.osr` and it judges the replay, then holds its own totals up
against the ones osu! wrote into the file's header. The header is the only
ground truth we have, so this is the test that actually says whether the
simulator is right — synthetic tests only say it does what I think it does.

Deliberately not localised: the whole router is gated to render testers (see
`utils.render_access`), and the audience for a debugging read-out is one person.
"""

import asyncio
import os
import tempfile
from time import monotonic

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.dossier import renders
from db.database import get_db_session
from config.settings import TELEGRAM_BOT_API_URL
from services import dossier
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="dossier")

# Replays are tiny — a long one is a few hundred KB. Anything much larger isn't
# a replay, and downloading it would just be someone else's bandwidth.
_MAX_REPLAY_BYTES = 8 * 1024 * 1024

def _max_video_bytes() -> int:
    """What this deployment can actually send.

    The cloud Bot API stops at 50 MB; a self-hosted one raises that to ~2 GB,
    which is why `TELEGRAM_BOT_API_URL` exists. Hardcoding the small number
    refused files the bot was perfectly able to send.

    Read at call time rather than at import so the answer follows the config
    instead of whatever was true when the module loaded.
    """
    if TELEGRAM_BOT_API_URL:
        return 2000 * 1024 * 1024
    return 48 * 1024 * 1024


@router.message(Command("dossier"))
async def on_status(message: types.Message) -> None:
    if not dossier.is_available():
        await message.reply(
            "Dossier: движок не собран.\n"
            "<code>cd dossier &amp;&amp; cargo build --release</code>",
            parse_mode="HTML",
        )
        return
    await message.reply(
        "Dossier готов. Пришли <code>.osr</code> файлом — прогоню судейство "
        "и сверю с заголовком реплея.",
        parse_mode="HTML",
    )


@router.message(F.document)
async def on_replay_document(
    message: types.Message, osu_api_client=None, tenant_chat_id=None
) -> None:
    document = message.document
    name = (document.file_name or "").lower()
    if not name.endswith(".osr"):
        return
    if document.file_size and document.file_size > _MAX_REPLAY_BYTES:
        await message.reply("Это не похоже на реплей — слишком большой файл.")
        return

    status = await message.reply("Читаю реплей…")

    with tempfile.TemporaryDirectory(prefix="dossier-") as workdir:
        replay_path = os.path.join(workdir, "replay.osr")
        try:
            await message.bot.download(document, destination=replay_path)
        except Exception as exc:  # noqa: BLE001 — Telegram download, many shapes
            logger.warning("replay download failed: %s", exc)
            await status.edit_text(f"Не удалось скачать файл: {exc}")
            return

        try:
            header = await dossier.inspect(replay_path)
        except dossier.DossierError as exc:
            await status.edit_text(str(exc))
            return

        if "error" in header:
            await status.edit_text(f"Реплей не разобрался: {header['error']}")
            return
        if header.get("mode") != "Standard":
            await status.edit_text(
                f"Пока только osu!standard, а тут {header.get('mode', '?')}."
            )
            return
        if not header.get("frames"):
            await status.edit_text("В реплее нет кадров — судить нечего.")
            return

        await status.edit_text(
            f"{header['player']} · {header['mods']} · "
            f"{header['frames']} кадров. Ищу карту…"
        )

        try:
            beatmap = await dossier.ensure_map(osu_api_client, header["beatmap_hash"])
        except dossier.MapUnavailable as exc:
            await status.edit_text(str(exc))
            return

        await status.edit_text(f"{dossier.describe(beatmap)}\nСужу…")

        try:
            result = await dossier.judge(replay_path, dossier.songs_dir())
        except dossier.DossierError as exc:
            await status.edit_text(str(exc))
            return

        if "error" in result:
            await status.edit_text(f"Судейство не состоялось: {result['error']}")
            return

        # Copied out before the temporary directory goes, so the buttons below
        # still have something to work from. The verdict goes with it: every
        # section is drawn from this dict on demand rather than from one string
        # built now and sliced later.
        result["api_max_combo"] = (beatmap or {}).get("max_combo")
        # Kept for the scoreboard: the render happens minutes later behind a
        # button, by which point the beatmap record is long out of scope.
        result["beatmap_id"] = (beatmap or {}).get("id")
        # The *tenant*, not the chat this message arrived in. In a group they are
        # the same; in a DM they are not, and `message.chat.id` there is the
        # private conversation — which no registered player's `chat_id` matches,
        # so the scoreboard came out empty and looked like a broken feature
        # rather than a question asked about the wrong chat. Every other data
        # handler in the bot reads `tenant_chat_id` for exactly this reason.
        #
        # Left as None rather than falling back, because there is no useful
        # fallback: judging a replay needs no chat at all, and a scoreboard needs
        # a real one. None means "say why there is no scoreboard".
        result["chat_id"] = tenant_chat_id
        result["beatmap_status"] = (beatmap or {}).get("status")
        # Which arithmetic the board is drawn in. The engine computes the
        # player's own row in the replay's own scoring, so the rivals have to be
        # asked for the matching field or the columns are not the same units.
        result["lazer"] = str(result.get("client", "")).startswith("lazer")
        result["no_audio"] = bool((beatmap or {}).get("_no_audio"))
        token = renders.remember(replay_path, dossier.describe(beatmap), result)

    await status.edit_text(
        _format(result, dossier.describe(beatmap)),
        parse_mode="HTML",
        reply_markup=_verdict_keyboard(token, result),
    )


def _format(result: dict, map_name: str) -> str:
    """The answer, and only the answer.

    Everything that explains *why* now lives behind a button. This message is
    read every time a replay is sent and the explanations are read when
    something looks wrong, which is a different frequency and deserves a
    different place: five paragraphs under a table nobody has finished reading
    yet is five paragraphs nobody reads.
    """
    ours, theirs = result["ours"], result["theirs"]
    rows = [
        ("300", ours["300"], theirs["300"]),
        ("100", ours["100"], theirs["100"]),
        ("50", ours["50"], theirs["50"]),
        ("промах", ours["miss"], theirs["miss"]),
        ("комбо", result["our_max_combo"], result["their_max_combo"]),
    ]

    lines = [f"{'':>7}{'наше':>7}{'осу':>7}"]
    for label, mine, real in rows:
        mark = "" if mine == real else "  ←"
        lines.append(f"{label:>7}{mine:>7}{real:>7}{mark}")
    acc_mark = "" if abs(result["our_accuracy"] - result["their_accuracy"]) < 0.005 else "  ←"
    lines.append(
        f"{'точн.':>7}{result['our_accuracy']:>6.2f}%{result['their_accuracy']:>6.2f}%{acc_mark}"
    )

    verdict = "Сходится полностью." if result["exact"] else "Расхождение."
    header = f"<b>{map_name}</b>\n{result['player']} · {result['mods']} · {result['objects']} объектов"
    # The one thing that cannot wait for a button: a table covering 802 of 1894
    # objects under a heading that says 1894 is misread in the first second.
    return f"{header}\n<pre>{chr(10).join(lines)}</pre>{verdict}{_explain_early_end(result)}"


# Which sections have anything to say about this replay. A button that opens an
# empty page is worse than no button: it costs a tap to learn nothing.
_SECTIONS: list[tuple[str, str]] = [
    ("misses", "🎯 Промахи"),
    ("score", "🏆 Очки"),
    ("combo", "🔗 Комбо"),
    ("tails", "🌀 Хвосты"),
]


def _section_text(key: str, result: dict) -> str:
    if key == "misses":
        return _explain_misses(result.get("misses"))
    if key == "score":
        return _compare_score(result)
    if key == "combo":
        return _compare_combo_ceiling(result, result.get("api_max_combo"))
    if key == "tails":
        return _explain_tails(result)
    return ""


def _verdict_keyboard(token: str, result: dict) -> InlineKeyboardMarkup:
    rows = []
    available = [
        InlineKeyboardButton(text=label, callback_data=f"dsa:{token}:{key}")
        for key, label in _SECTIONS
        if _section_text(key, result).strip()
    ]
    # Two to a row: four full-width buttons push the render row off the first
    # screen on a phone, and the render is what most of these end in.
    for i in range(0, len(available), 2):
        rows.append(available[i : i + 2])
    rows.append(
        [
            InlineKeyboardButton(text="🎬 Рендер", callback_data=f"dsr:{token}"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"dss:{token}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("dsa:"))
async def on_section(callback: types.CallbackQuery) -> None:
    _, token, key = callback.data.split(":", 2)
    pending = renders.get(token)
    if not pending or not pending.verdict:
        await callback.answer("Разбор уже не хранится — пришли реплей заново.", show_alert=True)
        return
    text = _section_text(key, pending.verdict).strip()
    if not text:
        await callback.answer("Тут сказать нечего.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(text, parse_mode="HTML")


def _explain_early_end(result: dict) -> str:
    """Say when the table covers less than the map.

    Without this the message headed a table of 802 judgements with "1894
    объектов" and said nothing else, so the only available reading was that the
    engine had lost a thousand objects. It had not: the player died, both
    columns stop where the play stopped, and the comparison is honest — it is
    just a comparison of a fragment.
    """
    if result.get("finished", True):
        return ""
    judged, objects = result.get("judged"), result.get("objects")
    if not judged or not objects:
        return ""
    return (
        f"\n\nИгра оборвалась: сыграно {judged} из {objects} объектов."
        " Обе колонки считают только их — остальная карта вне сравнения."
    )


def _compare_score(result: dict) -> str:
    """How far our score is from the one the replay carries.

    A separate reading from the counts, and it moves on its own: a play whose
    four totals are exact can still be scored wrong, which is how a failed
    lazer play scoring to the end of the map went unnoticed for weeks.
    """
    off = result.get("score_error")
    if off is None:
        return ""
    if abs(off) < 0.05:
        return f"\n\nОчки сходятся ({off:+.2f}%)."
    return f"\n\nОчки расходятся на {off:+.2f}%."


def _compare_combo_ceiling(result: dict, api_max_combo: int | None) -> str:
    """Check our part count against the map's published max combo.

    This is the only figure in the whole comparison that doesn't depend on the
    replay, so it splits the search cleanly: if the ceilings disagree we're
    building sliders out of the wrong number of pieces, and no amount of
    tuning the tracking rules would ever fix it.
    """
    ours = result.get("max_possible_combo")
    if not ours or not api_max_combo:
        return ""
    if ours == api_max_combo:
        return f"\n\nПотолок комбо совпал ({ours}) — части считаем верно."
    return (
        f"\n\nПотолок комбо: у нас {ours}, у osu! {api_max_combo}"
        f" ({ours - api_max_combo:+}). Расходимся в числе частей, а не в вердиктах."
    )


def _explain_tails(result: dict) -> str:
    """Size the pool of sliders the tail lenience is deciding.

    When we hand out more 300s than the replay does, the tails we credited only
    because of the 36ms grace window are the sliders that could account for it.
    If that pool is smaller than the disagreement, the lenience is innocent and
    the cause is somewhere else — which is worth knowing before tuning it.
    """
    lenient = result.get("lenient_tails")
    if not lenient or result.get("counts_match"):
        return ""
    gap = result["ours"]["300"] - result["theirs"]["300"]
    if gap <= 0:
        return ""
    rim = result.get("tails_near_the_rim", 0)
    return (
        f"\n\nЛишних трёхсоток: {gap}."
        f" Хвостов на допуске по времени: {lenient}, по краю фолловкруга: {rim}."
    )


def _explain_misses(misses: dict | None) -> str:
    """Say what our misses have in common.

    A miss with a click right beside it means the object is in the wrong place —
    our bug. A miss with no click near it is the player's, and the engine is
    only echoing it. Without this line the two are indistinguishable in a
    totals table, and every mismatch looks equally alarming.
    """
    if not misses:
        return ""
    total = misses["circle"] + misses["slider"] + misses["spinner"]
    if not total:
        return ""

    kinds = ", ".join(
        f"{label} {misses[key]}"
        for key, label in (("circle", "круги"), ("slider", "слайдеры"), ("spinner", "спиннеры"))
        if misses[key]
    )
    lines = [f"\n\nНаши промахи: {total} ({kinds})."]

    # Spinners are judged by rotation, so a click-based explanation would be
    # nonsense for them. Their own numbers say which side is wrong: a steady
    # fraction of the requirement means the requirement is off, near-zero means
    # the counting is.
    done, needed = misses.get("spin_rotations"), misses.get("spin_required")
    if misses["spinner"] and done is not None and needed:
        lines.append(
            f" Спиннеры: в среднем накрутили {done:.1f} из {needed:.1f} оборотов"
            f" ({done / needed * 100:.0f}%)."
        )

    if misses["circle"] or misses["slider"]:
        suspects = misses["geometry_suspects"]
        if suspects:
            overshoot = misses.get("median_overshoot_px")
            detail = f" на ~{overshoot:.1f} px" if overshoot is not None else ""
            lines.append(f" Из них {suspects} — клик был рядом, но чуть мимо круга{detail}.")
        elif misses["with_nearby_click"]:
            lines.append(f" У {misses['with_nearby_click']} клик рядом был, но далеко от круга.")
        else:
            lines.append(" Кликов рядом не было — похоже, это промахи игрока.")
    return "".join(lines)


# What each setting can be, in the order the buttons appear. Kept as data so
# the screen, the callback that sets a value and the check that a value is legal
# are all one list — three places that have to agree are one place with two
# hazards attached.
_OPTIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "size": (
        "Размер",
        [("854x480", "480p"), ("1280x720", "720p"), ("1920x1080", "1080p")],
    ),
    "fps": ("Кадры", [("30", "30"), ("60", "60")]),
    "mute": ("Звук", [("0", "со звуком"), ("1", "без звука")]),
}


def _settings_keyboard(token: str, choices: renders.Choices) -> InlineKeyboardMarkup:
    rows = []
    for key, (label, values) in _OPTIONS.items():
        current = str(getattr(choices, key))
        if key == "mute":
            current = "1" if choices.mute else "0"
        rows.append(
            [
                InlineKeyboardButton(
                    # The chosen one is marked rather than hidden. A settings
                    # screen that only shows what you can change makes you tap
                    # something to find out what is already true.
                    text=f"{'● ' if value == current else ''}{shown}",
                    callback_data=f"dsv:{token}:{key}:{value}",
                )
                for value, shown in values
            ]
        )
        rows[-1].insert(0, InlineKeyboardButton(text=f"{label}:", callback_data="dsn"))
    rows.append([InlineKeyboardButton(text="🎬 Рендерить", callback_data=f"dsr:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "dsn")
async def on_label(callback: types.CallbackQuery) -> None:
    """The row labels are buttons because Telegram has no other way to put text
    on a keyboard row. Tapping one should do nothing, quietly."""
    await callback.answer()


@router.callback_query(F.data.startswith("dss:"))
async def on_settings(callback: types.CallbackQuery) -> None:
    token = callback.data.split(":", 1)[1]
    if not renders.get(token):
        await callback.answer("Реплей уже не хранится — пришли его заново.", show_alert=True)
        return
    await callback.answer()
    choices = renders.choices(callback.from_user.id)
    await callback.message.answer(
        f"<b>Настройки рендера</b>\n{choices.summary()}",
        parse_mode="HTML",
        reply_markup=_settings_keyboard(token, choices),
    )


@router.callback_query(F.data.startswith("dsv:"))
async def on_set_value(callback: types.CallbackQuery) -> None:
    _, token, key, value = callback.data.split(":", 3)
    if key not in _OPTIONS or value not in {v for v, _ in _OPTIONS[key][1]}:
        await callback.answer("Такой настройки нет.", show_alert=True)
        return
    choices = renders.choices(callback.from_user.id)
    if key == "mute":
        choices.mute = value == "1"
    elif key == "fps":
        choices.fps = int(value)
    else:
        choices.size = value

    await callback.answer(choices.summary())
    try:
        await callback.message.edit_text(
            f"<b>Настройки рендера</b>\n{choices.summary()}",
            parse_mode="HTML",
            reply_markup=_settings_keyboard(token, choices),
        )
    except Exception as exc:  # noqa: BLE001 — an unchanged message is not an error
        logger.debug("settings edit failed: %s", exc)


@router.callback_query(F.data.startswith("dsx:"))
async def on_cancel(callback: types.CallbackQuery) -> None:
    """Call off a render in flight.

    Minutes on one core, and the commonest reason to want it back is having
    picked the wrong size — which is exactly the moment when waiting it out is
    the most annoying thing the bot could ask for.
    """
    pending = renders.get(callback.data.split(":", 1)[1])
    if not pending or not pending.task or pending.task.done():
        await callback.answer("Уже нечего отменять.", show_alert=True)
        return
    pending.task.cancel()
    await callback.answer("Отменяю…")


@router.callback_query(F.data.startswith("dsr:"))
async def on_render(callback: types.CallbackQuery, osu_api_client=None) -> None:
    token = callback.data.split(":", 1)[1]
    pending = renders.get(token)
    if not pending:
        await callback.answer("Реплей уже не хранится — пришли его заново.", show_alert=True)
        return

    if renders.render_lock.locked():
        await callback.answer("Уже рендерю другой реплей, подожди.", show_alert=True)
        return

    choices = renders.choices(callback.from_user.id)
    size = choices.size
    await callback.answer()

    # The message comes first and the gathering second. A chat's worth of score
    # lookups goes through a rate limiter one at a time and measured at about a
    # minute — and for that minute the bot said nothing at all, so pressing
    # Render looked like pressing nothing. Now it says what it is doing, and
    # counts.
    status = await callback.message.answer(
        "Собираю скорборд беседы…", reply_markup=_cancel_keyboard(token)
    )
    rivals = await _gather_rivals(pending.verdict, osu_api_client, status)
    warning = ""
    if pending.verdict.get("no_audio"):
        warning += (
            "\n⚠️ Архив карты не достался ни с одного зеркала — карта взята напрямую "
            "у osu!, так что видео выйдет без музыки."
        )
    if not rivals:
        warning += "\nℹ️ " + _why_no_scoreboard(pending.verdict)
    await status.edit_text(
        f"Рендерю {choices.summary()}… это займёт минуты.{warning}",
        reply_markup=_cancel_keyboard(token),
    )
    out_path = os.path.join(pending.workdir, "replay.mp4")

    async with renders.render_lock:
        watch = _progress_watcher(status, size)
        # Run as a task rather than awaited directly, so the cancel button has
        # something to cancel. The engine kills its own child on the way out.
        pending.task = asyncio.create_task(
            dossier.video(
                pending.replay_path,
                dossier.songs_dir(),
                out_path,
                size=size,
                fps=choices.fps,
                mute=choices.mute,
                skin=choices.skin,
                leaderboard=rivals,
                on_progress=watch,
            )
        )
        try:
            report = await pending.task
        except asyncio.CancelledError:
            await status.edit_text("Рендер отменён.", reply_markup=_again_keyboard(token))
            return
        except dossier.DossierError as exc:
            await status.edit_text(
                f"Рендер не удался.\n<pre>{_escape(str(exc).splitlines())}</pre>",
                parse_mode="HTML",
                reply_markup=_again_keyboard(token),
            )
            return
        finally:
            pending.task = None

    pending.report = report.report
    size_bytes = os.path.getsize(out_path)
    megabytes = size_bytes / 1024 / 1024
    if size_bytes > _max_video_bytes():
        await status.edit_text(
            f"Готово, но файл {megabytes:.0f} МБ — больше, чем этот Bot API принимает.\n"
            f"Лежит на хосте: <code>{out_path}</code>",
            parse_mode="HTML",
            reply_markup=_summary_keyboard(token),
        )
        return

    await status.edit_text(f"Готово — {megabytes:.1f} МБ, {size}. Отправляю…")
    try:
        await callback.message.answer_video(
            types.FSInputFile(out_path),
            caption=pending.title,
            supports_streaming=True,
            # Told, not guessed. Telegram lays the placeholder out from these
            # and not from the stream, so a video sent without them arrives as
            # a square on a phone — desktop happens to correct itself once
            # playback starts, which is what made it look like a player bug.
            width=report.width,
            height=report.height,
            duration=report.duration,
        )
    except Exception as exc:  # noqa: BLE001 — upload failures come in many shapes
        logger.warning("video upload failed: %s", exc)
        # The render is done and on disk; say where, so the work isn't lost to
        # a failed upload.
        await status.edit_text(
            f"Отрендерил ({megabytes:.1f} МБ), но отправить не вышло: {exc}\n"
            f"Файл на хосте: <code>{out_path}</code>",
            parse_mode="HTML",
            reply_markup=_summary_keyboard(token),
        )
        return

    # The summary goes behind a button rather than above the video. It is the
    # only account of how the render went and worth reading — afterwards, by
    # someone who went looking for it, not stacked on top of the thing they
    # actually asked for.
    await status.edit_text(
        f"Отправлено — {megabytes:.1f} МБ, {report.width}×{report.height}, "
        f"{report.duration or 0} с.",
        reply_markup=_summary_keyboard(token),
    )


# One gather per map per chat, for as long as the process lives. Re-rendering the
# same replay at another size is the commonest thing anybody does with the Again
# button, and paying a minute of rate-limited lookups for an answer we had thirty
# seconds ago is the sort of cost nobody reports as a bug and everybody feels.
_scoreboards: dict[tuple[int, int], str] = {}


async def _gather_rivals(verdict: dict, client, status=None) -> str:
    """The chat's own scoreboard for this map, or nothing.

    Best-effort throughout. A scoreboard is a decoration on a render that took
    minutes to produce, and losing the render because the osu! API was slow
    would be the wrong trade — so every failure here ends in an empty string,
    which the engine reads as "draw no scoreboard".
    """
    beatmap_id, chat_id = verdict.get("beatmap_id"), verdict.get("chat_id")
    if not beatmap_id or not chat_id or client is None:
        return ""
    cached = _scoreboards.get((chat_id, beatmap_id))
    if cached is not None:
        return cached

    last = 0.0

    async def tick(done: int, total: int) -> None:
        nonlocal last
        now = monotonic()
        # Telegram rate-limits edits, and this can tick forty times.
        if now - last < 3.0 or status is None:
            return
        last = now
        try:
            await status.edit_text(f"Собираю скорборд беседы… {done}/{total}")
        except Exception as exc:  # noqa: BLE001 — a failed edit must not stop it
            logger.debug("scoreboard progress edit failed: %s", exc)

    try:
        async with get_db_session() as session:
            board = await dossier.collect_rivals(
                client,
                session,
                chat_id,
                beatmap_id,
                verdict.get("beatmap_status"),
                tick,
                bool(verdict.get("lazer")),
            )
    except Exception as exc:  # noqa: BLE001 — DB or API, and neither is worth a render
        logger.warning("could not build the scoreboard: %s", exc)
        return ""
    _scoreboards[(chat_id, beatmap_id)] = board
    return board


def _why_no_scoreboard(verdict: dict) -> str:
    """Name the reason rather than leaving the left of the frame bare.

    An empty scoreboard has four quite different causes and they call for four
    different responses — choose a chat, expect nothing, wait for someone to
    play it, or come and look at a bug. Drawing nothing and saying nothing makes
    all four look like the last one.
    """
    if not verdict.get("chat_id"):
        return (
            "Скорборда нет: в личке бот не знает, чью беседу сравнивать. "
            "Пришли реплей в беседу или выбери её для лички."
        )
    status = (verdict.get("beatmap_status") or "").lower()
    if status and status not in ("ranked", "approved", "qualified", "loved"):
        return f"Скорборда нет: у карты статус {status}, у osu! на такие нет таблицы."
    return "Скорборда нет: ни у кого из беседы нет счёта на этой карте."


def _cancel_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✖️ Отменить", callback_data=f"dsx:{token}")]]
    )


def _again_keyboard(token: str) -> InlineKeyboardMarkup:
    """After a render that did not produce a video. The replay is still here, so
    the next attempt costs a tap rather than another upload."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Ещё раз", callback_data=f"dsr:{token}"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"dss:{token}"),
            ]
        ]
    )


def _summary_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Итоги рендера", callback_data=f"dsm:{token}")],
            [
                InlineKeyboardButton(text="🎬 Ещё раз", callback_data=f"dsr:{token}"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"dss:{token}"),
            ],
        ]
    )


@router.callback_query(F.data.startswith("dsm:"))
async def on_summary(callback: types.CallbackQuery) -> None:
    pending = renders.get(callback.data.split(":", 1)[1])
    if not pending or not pending.report:
        await callback.answer("Итогов уже нет — реплей выселен из памяти.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"<pre>{_escape(pending.report)}</pre>", parse_mode="HTML"
    )


# Telegram rate-limits edits, and a render ticks several times a second. Editing
# on a timer rather than on every tick keeps the message alive without spending
# the whole budget on a progress bar.
_PROGRESS_EVERY_SECONDS = 8.0


def _left(seconds: float) -> str:
    """How long is left, in units that still say something at the end.

    Rounded to whole minutes, the last minute and a half of every render reads
    "~1 мин" and then "~0 мин", which is the stretch where somebody is actually
    watching. Seconds carry all the way down; minutes only appear once there
    are any.
    """
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} с"
    return f"{seconds // 60} мин {seconds % 60:02d} с"


def _progress_watcher(status: types.Message, size: str):
    """Put the engine's own progress into the status message.

    A render is minutes long and until now said nothing while it ran, so a slow
    one and a wedged one looked identical from the outside — which is precisely
    the thing that needed telling apart on a one-core box.
    """
    last = 0.0

    async def watch(progress) -> None:
        nonlocal last
        now = monotonic()
        if now - last < _PROGRESS_EVERY_SECONDS:
            return
        last = now
        filled = round(progress.fraction * 12)
        bar = "█" * filled + "░" * (12 - filled)
        try:
            await status.edit_text(
                f"Рендерю {size}\n"
                f"<code>{bar}</code> {progress.fraction * 100:.0f}%\n"
                f"{progress.done}/{progress.total} кадров · {progress.fps:.0f}/с · "
                f"осталось ~{_left(progress.seconds_left)}",
                parse_mode="HTML",
            )
        except Exception as exc:  # noqa: BLE001 — a failed edit must not stop a render
            logger.debug("progress edit failed: %s", exc)

    return watch


def _escape(lines: list[str]) -> str:
    """Whatever the engine says lands inside a <pre>, so its angle brackets
    have to stop being markup."""
    text = "\n".join(lines) or "(движок ничего не сообщил)"
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
