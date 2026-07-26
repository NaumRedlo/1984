"""Throwing a real replay at the engine.

Send the bot an `.osr` and it judges the replay, then holds its own totals up
against the ones osu! wrote into the file's header. The header is the only
ground truth we have, so this is the test that actually says whether the
simulator is right — synthetic tests only say it does what I think it does.

Deliberately not localised: the whole router is gated to render testers (see
`utils.render_access`), and the audience for a debugging read-out is one person.
"""

import os
import tempfile

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.dossier import renders
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
async def on_replay_document(message: types.Message, osu_api_client=None) -> None:
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

        # Copied out before the temporary directory goes, so the button below
        # still has something to render.
        token = renders.remember(replay_path, dossier.describe(beatmap))

    await status.edit_text(
        _format(result, dossier.describe(beatmap), (beatmap or {}).get("max_combo")),
        parse_mode="HTML",
        reply_markup=_render_keyboard(token),
    )


def _format(result: dict, map_name: str, api_max_combo: int | None = None) -> str:
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
    tail = (
        _explain_misses(result.get("misses"))
        + _compare_combo_ceiling(result, api_max_combo)
        + _explain_tails(result)
    )
    return f"{header}\n<pre>{chr(10).join(lines)}</pre>{verdict}{tail}"


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


def _render_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 720p", callback_data=f"dsr:{token}:1280x720"),
                InlineKeyboardButton(text="🎬 1080p", callback_data=f"dsr:{token}:1920x1080"),
            ]
        ]
    )


@router.callback_query(F.data.startswith("dsr:"))
async def on_render(callback: types.CallbackQuery) -> None:
    _, token, size = callback.data.split(":", 2)
    pending = renders.get(token)
    if not pending:
        await callback.answer("Реплей уже не хранится — пришли его заново.", show_alert=True)
        return

    if renders.render_lock.locked():
        await callback.answer("Уже рендерю другой реплей, подожди.", show_alert=True)
        return

    await callback.answer()
    status = await callback.message.answer(f"Рендерю {size}… это займёт минуты.")
    out_path = os.path.join(pending.workdir, "replay.mp4")

    async with renders.render_lock:
        try:
            report = await dossier.video(
                pending.replay_path, dossier.songs_dir(), out_path, size=size
            )
        except dossier.DossierError as exc:
            await status.edit_text(f"Рендер не удался: {exc}")
            return

    size_bytes = os.path.getsize(out_path)
    megabytes = size_bytes / 1024 / 1024
    if size_bytes > _max_video_bytes():
        await status.edit_text(
            f"Готово, но файл {megabytes:.0f} МБ — больше, чем этот Bot API принимает.\n"
            f"Лежит на хосте: <code>{out_path}</code>",
            parse_mode="HTML",
        )
        return

    await status.edit_text(
        f"Готово, {megabytes:.1f} МБ. Отправляю…\n<pre>{_escape(report)}</pre>",
        parse_mode="HTML",
    )
    try:
        await callback.message.answer_video(
            types.FSInputFile(out_path),
            caption=pending.title,
            supports_streaming=True,
        )
    except Exception as exc:  # noqa: BLE001 — upload failures come in many shapes
        logger.warning("video upload failed: %s", exc)
        # The render is done and on disk; say where, so the work isn't lost to
        # a failed upload.
        await status.edit_text(
            f"Отрендерил ({megabytes:.1f} МБ), но отправить не вышло: {exc}\n"
            f"Файл на хосте: <code>{out_path}</code>",
            parse_mode="HTML",
        )
        return

    # The report outlives the upload: it is the only account of how the render
    # went, and the point of the whole exercise is reading it.
    await status.edit_text(f"<pre>{_escape(report)}</pre>", parse_mode="HTML")
    renders.forget(token)


def _escape(lines: list[str]) -> str:
    """Whatever the engine says lands inside a <pre>, so its angle brackets
    have to stop being markup."""
    text = "\n".join(lines) or "(движок ничего не сообщил)"
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
