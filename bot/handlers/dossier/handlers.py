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

from services import dossier
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="dossier")

# Replays are tiny — a long one is a few hundred KB. Anything much larger isn't
# a replay, and downloading it would just be someone else's bandwidth.
_MAX_REPLAY_BYTES = 8 * 1024 * 1024


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

    await status.edit_text(_format(result, dossier.describe(beatmap)), parse_mode="HTML")


def _format(result: dict, map_name: str) -> str:
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
    return f"{header}\n<pre>{chr(10).join(lines)}</pre>{verdict}"
