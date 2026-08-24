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
from db.models.user import User
from utils.osu.resolve_user import get_registered_user
from sqlalchemy import func, select
from config.settings import MAX_SKIN_MB, TELEGRAM_BOT_API_URL
from services import dossier
from services.dossier import skins
from services.render_farm import dispatch as render_farm
from utils.formatting.text import escape_html, plural as _plural
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="dossier")

# Replays are tiny — a long one is a few hundred KB. Anything much larger isn't
# a replay, and downloading it would just be someone else's bandwidth.
_MAX_REPLAY_BYTES = 8 * 1024 * 1024

def _max_incoming_bytes() -> int:
    """The largest file this deployment can be *handed*.

    The other side of `_max_video_bytes`. The cloud Bot API will not serve a
    file over 20 MB through `getFile` however large the upload was allowed to
    be; a self-hosted one goes to the same ~2 GB it accepts.

    Read at call time for the same reason as the sending limit: the answer
    follows the config rather than whatever was true when the module loaded.
    """
    if TELEGRAM_BOT_API_URL:
        return 2000 * 1024 * 1024
    return 20 * 1024 * 1024


def _max_skin_bytes() -> int:
    """The largest `.osk` we will take, which is two limits at once.

    A skin with high-resolution elements and a full hit-sound set really does
    reach three figures of megabytes, so `MAX_SKIN_MB` is where the deployment
    says what its disk can hold. But there is no point accepting more than
    Telegram will hand over: the old fixed 32 MB sat *above* the cloud API's
    20, so a 25 MB skin passed this check and then failed at the download with
    an error about something else entirely.
    """
    return min(MAX_SKIN_MB * 1024 * 1024, _max_incoming_bytes())

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
    if name.endswith(".osk"):
        await _take_skin(message, document)
        return
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

    await _answer_with_card(message, status, result, beatmap, token, osu_api_client)


async def _answer_with_card(
    message, status, result: dict, beatmap, token: str, osu_api_client=None
) -> None:
    """The play as a picture, with the engine's reading behind the buttons.

    A replay is the same event `rs` draws a card for — a player, a map, four
    counts and a combo — so it gets the same card rather than a table of
    figures. What the engine *thinks* of those counts is a different question,
    asked when something looks wrong rather than every time, and it lives under
    the buttons where it always did.

    The table is still the answer when there is no card to draw: an unranked
    map, an API that did not answer, a font that would not load. A render is
    the thing most of these end in and it must not be lost to a picture.
    """
    keyboard = _verdict_keyboard(token, result)
    try:
        photo = await _result_card(result, beatmap, message, osu_api_client)
    except Exception as exc:  # noqa: BLE001 — drawing, fonts, network: many shapes
        logger.warning("result card failed, falling back to the table: %s", exc)
        photo = None

    if photo is None:
        await status.edit_text(
            _format(result, dossier.describe(beatmap)),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # A text message cannot become a photo, so the waiting line goes and the
    # card arrives in its place.
    try:
        await status.delete()
    except Exception:  # noqa: BLE001 — already gone, or too old to delete
        pass
    await message.answer_photo(photo=photo, reply_markup=keyboard)


async def _result_card(result: dict, beatmap, message, osu_api_client=None):
    """The card itself, or `None` when this play cannot be drawn as one."""
    from aiogram.types import BufferedInputFile

    from services.dossier.card import score_from_replay
    from services.image import card_renderer
    from services.image.render.recent import build_recent_card_data

    if not beatmap:
        return None
    player, cover = await _who_played(result, osu_api_client)
    score = score_from_replay(result, beatmap)
    # This engine's own figure, off the `.osu` the judge already read — no
    # network, and no waiting on ppy to have a score to look up. A replay
    # somebody sent has never been submitted, so there is nothing to look up
    # anyway; the alternative to computing it is a blank.
    graded = await _assay(result, score)
    if graded and graded.get("pp") is not None:
        score["pp"] = graded["pp"]
    data = await build_recent_card_data(
        score,
        username=result.get("player", "") or "?",
        player_id=player,
        player_cover_url=cover,
        requester_name=(
            message.from_user.first_name or message.from_user.username or "?"
        ),
        card_mode="shared",
    )
    buffer = await card_renderer.generate_recent_card_async(data)
    return BufferedInputFile(buffer.read(), filename="replay.png")


async def _who_played(result: dict, osu_api_client) -> tuple[int, str]:
    """The osu! id and profile banner of whoever made this replay.

    A replay names its player and nothing else — no id, no pictures — so the
    card had an empty circle where a face goes and a flat panel where a banner
    does. The name is enough to ask with.

    Nothing here is worth failing a card over. A player who has since been
    renamed, or restricted, or who never existed under that name because the
    replay was made offline, gets the card without a face rather than no card.
    """
    name = (result.get("player") or "").strip()
    if not name or osu_api_client is None:
        return 0, ""
    try:
        found = await osu_api_client.get_user_data(name)
    except Exception as exc:  # noqa: BLE001 — the network, and ppy's shapes
        logger.debug("could not look up %s: %s", name, exc)
        return 0, ""
    if not found:
        return 0, ""
    return int(found.get("id") or 0), found.get("cover_url") or ""


async def _assay(result: dict, score: dict):
    """What `dossier assay` makes of this play, or `None` if it could not say.

    Off the map file the judge was pointed at, which is on disk and stays there
    — the replay's own copy goes with the temporary folder, the map does not.
    """
    from pathlib import Path

    from utils.osu.assay import assay

    source = result.get("map_source")
    if not source or not Path(source).exists():
        return None
    stats = score["statistics"]
    return await assay(
        Path(source),
        result.get("mods", ""),
        count_300=stats["count_300"],
        count_100=stats["count_100"],
        count_50=stats["count_50"],
        misses=stats["count_miss"],
        combo=score["max_combo"],
    )


async def _take_skin(message: types.Message, document) -> None:
    """Store a skin somebody sent, so renders can be made in it.

    The archive is a stranger's zip and is treated as one — see
    `services.dossier.skins`, which does the unpacking and the refusing. This
    only fetches the file and says what happened.
    """
    if document.file_size and document.file_size > _max_skin_bytes():
        # Which of the two limits it hit, because they call for opposite
        # answers: one is this bot's disk and the other is Telegram's, and
        # being told "we do not take those" about a limit somebody could lift
        # by running their own API server is being told the wrong thing.
        megabytes = _max_skin_bytes() // 1024 // 1024
        if not TELEGRAM_BOT_API_URL and MAX_SKIN_MB * 1024 * 1024 > _max_incoming_bytes():
            await message.reply(
                f"Скин больше {megabytes} МБ — столько Telegram нам не отдаёт. "
                "Это предел облачного Bot API, а не наш."
            )
        else:
            await message.reply(f"Скин больше {megabytes} МБ — столько мы не берём.")
        return

    status = await message.reply("Забираю скин…")
    with tempfile.TemporaryDirectory(prefix="dossier-skin-") as workdir:
        archive = os.path.join(workdir, "skin.osk")
        try:
            await message.bot.download(document, destination=archive)
        except Exception as exc:  # noqa: BLE001 — Telegram download, many shapes
            logger.warning("skin download failed: %s", exc)
            await status.edit_text(f"Не удалось скачать файл: {exc}")
            return

        try:
            name = await asyncio.to_thread(
                skins.import_osk,
                archive,
                document.file_name or "skin.osk",
                message.from_user.id if message.from_user else None,
            )
        except skins.SkinRejected as exc:
            await status.edit_text(f"Скин не принят: {exc}")
            return

    count = len(os.listdir(skins.folder_of(name) or "."))
    await status.edit_text(
        f"Скин <b>{escape_html(name)}</b> сохранён — {count} "
        f"{_plural(count, 'файл', 'файла', 'файлов')}.\n"
        f"Выбрать его для рендера: <code>sts</code> → Рендер.",
        parse_mode="HTML",
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
    """What to do with this play, and where the map is.

    The engine's own read-outs used to sit here — four buttons of windows,
    misses and slider ends, offered to everybody every time. They belong to
    somebody checking the engine rather than to somebody who sent a replay, and
    on a card that says the result plainly they were four taps of noise around
    the two that matter. `dossier judge --explain` still says all of it.

    What a card wants beside it instead is what every other card in this bot
    has: the map it is about, and the standings on it.
    """
    rows = [
        [
            InlineKeyboardButton(text="🎬 Отрендерить", callback_data=f"dsr:{token}"),
            InlineKeyboardButton(text="✂️ Экспозитор", callback_data=f"dse:{token}"),
        ]
    ]
    beatmap_id = result.get("beatmap_id")
    beside = []
    if beatmap_id:
        beside.append(
            InlineKeyboardButton(
                text="🗺 Карта",
                url=f"https://osu.ppy.sh/beatmaps/{beatmap_id}",
            )
        )
        beside.append(
            InlineKeyboardButton(text="🏆 Топ карты", callback_data=f"lbm:{beatmap_id}")
        )
    if beside:
        rows.append(beside)
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
# The render's own settings — size, frame rate, sound — used to be a keyboard
# hung off the replay, which meant they existed only while a replay did. They
# live in `sts` now, with the rest of a person's settings, and are read from
# there through `renders.choices`. See
# bot/handlers/profile/settings_menu/render.py.


@router.callback_query(F.data.startswith("dsr:"))
async def on_render(
    callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None
) -> None:
    await _render(callback, osu_api_client, reel=False, tenant_chat_id=tenant_chat_id)


@router.callback_query(F.data.startswith("dse:"))
async def on_exhibit(
    callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None
) -> None:
    """The telling moments of the play, cut into one short reel.

    The same render as the button beside it, over five spans instead of the
    whole map — which is why it shares every line of the body below rather than
    getting a copy of it. A copy is how the reel would end up wearing a
    different skin, or losing the scoreboard, the first time either changed.
    """
    await _render(callback, osu_api_client, reel=True, tenant_chat_id=tenant_chat_id)


async def _render(
    callback: types.CallbackQuery, osu_api_client, *, reel: bool, tenant_chat_id=None
) -> None:
    token = callback.data.split(":", 1)[1]
    pending = renders.get(token)
    if not pending:
        await callback.answer("Реплей уже не хранится — пришли его заново.", show_alert=True)
        return

    if renders.render_lock.locked():
        await callback.answer("Уже рендерю другой реплей, подожди.", show_alert=True)
        return

    # Read back from the row as well: the bot may have restarted since these
    # were last set, and a render in the wrong resolution because of that is a
    # render somebody has to ask for twice.
    choices = renders.choices(callback.from_user.id)
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        renders.restore_settings(user, choices)
        # Anything above 1080p60 is rationed, and this is where it is spent —
        # when a machine is actually about to draw for minutes. Counted before
        # the render rather than after: a render that fails halfway still cost
        # the minutes, and counting on success would make failing the cheap way
        # to spend nothing.
        #
        # The settings screen refuses the *choice* when there is no ration left,
        # so reaching here with none is the case where a day rolled over or the
        # last one was spent in another chat. Refused rather than quietly
        # downgraded: somebody who asked for 4K should be told they got none of
        # it, not handed 1080p and left to notice.
        if choices.heavy():
            if renders.heavy_left(user) <= 0:
                await callback.answer(
                    f"На сегодня рендеры выше 1080p60 закончились "
                    f"({renders.HEAVY_PER_DAY} в день). Выбери размер ниже в /sts.",
                    show_alert=True,
                )
                return
            renders.spend_heavy(user)
            await session.commit()
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
    faces = _faces_dir()
    rivals = await _gather_rivals(pending.verdict, osu_api_client, status, faces)
    mine = await _player_pictures(pending.verdict, faces, osu_api_client)
    warning = ""
    if pending.verdict.get("no_audio"):
        warning += (
            "\n⚠️ Архив карты не достался ни с одного зеркала — карта взята напрямую "
            "у osu!, так что видео выйдет без музыки."
        )
    if not rivals:
        warning += "\nℹ️ " + await _why_no_scoreboard(pending.verdict)
    # None for a full render, which has no moments to name. Bound before the
    # branch below rather than inside it: the caption reads it either way.
    selection = None
    # For a reel, ask what it will hold before rendering it. Choosing costs
    # seconds and rendering costs minutes, so the wait can at least say what it
    # is a wait for — and how long the result will be, which is not something
    # the caller sets any more.
    line = f"Рендерю {choices.summary()}… это займёт минуты."
    if reel:
        try:
            selection = await dossier.moments(pending.replay_path, dossier.songs_dir())
        except dossier.DossierError as exc:
            await status.edit_text(
                f"Экспозитор не справился.\n<pre>{_escape(str(exc).splitlines())}</pre>",
                parse_mode="HTML",
                reply_markup=_again_keyboard(token),
            )
            return
        found = len(selection.clips)
        line = (
            f"Экспозитор выбрал {found} "
            f"{_plural(found, 'момент', 'момента', 'моментов')} — "
            f"{selection.watch_seconds():.0f} с. Рендерю… это займёт минуты."
        )
    await status.edit_text(f"{line}{warning}", reply_markup=_cancel_keyboard(token))
    out_path = os.path.join(pending.workdir, "reel.mp4" if reel else "replay.mp4")

    # A stored skin is chosen by name and rendered by path: the engine takes a
    # folder. Resolved here rather than held as a path in the settings, so a
    # skin deleted between choosing and rendering falls back to the engine's
    # own look instead of pointing at nothing.
    chosen_skin = skins.folder_of(choices.skin) if choices.skin else None
    if choices.skin and skins.is_stale(choices.skin):
        # Rendered anyway — a skin unpacked by older code is wrong in places,
        # not unusable, and refusing would take away a render somebody asked
        # for. Said out loud so the answer is on record when the pictures look
        # off: send the `.osk` again.
        logger.warning(
            "skin %s was unpacked by an older importer; it should be sent again",
            choices.skin,
        )
    common = dict(
        size=size,
        fps=choices.fps,
        mute=choices.mute,
        skin=chosen_skin,
        # Withheld rather than drawn empty when it is switched off: the
        # engine draws whatever column it is handed, so "no scoreboard" has to
        # be no scoreboard reaching it.
        leaderboard=rivals if choices.leaderboard else None,
        my_pictures=mine,
        background=choices.background,
        bare=choices.bare,
        effects=choices.effects,
        music=choices.music,
        hitsounds=choices.hitsounds,
        map_hitsounds=choices.map_hitsounds,
        dim=choices.dim,
        meter=choices.meter,
        cursor=choices.cursor,
        blur=choices.blur,
        volume=choices.volume,
    )
    async with renders.render_lock:
        watch = _progress_watcher(status, size)
        # Run as a task rather than awaited directly, so the cancel button has
        # something to cancel. The engine kills its own child on the way out.
        # The selection was already asked for, above, to name the moments in
        # the status message — handed on rather than recomputed.
        common["title"] = pending.title
        if reel:
            common["chosen"] = selection
        engine = render_farm.exhibit if reel else render_farm.video
        pending.task = asyncio.create_task(
            engine(
                pending.replay_path,
                dossier.songs_dir(),
                out_path,
                on_progress=watch,
                **common,
            )
        )
        try:
            report = await pending.task
            if reel:
                # `exhibit` answers with both the reel and what it chose; the
                # rest of this function only knows about renders.
                report, selection = report.render, report.selection
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
            reply_markup=_summary_keyboard(token, result),
        )
        return

    await status.edit_text(f"Готово — {megabytes:.1f} МБ, {size}. Отправляю…")
    try:
        await callback.message.answer_video(
            types.FSInputFile(out_path),
            caption=_caption(pending.title, selection),
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
            reply_markup=_summary_keyboard(token, result),
        )
        return

    # The summary goes behind a button rather than above the video. It is the
    # only account of how the render went and worth reading — afterwards, by
    # someone who went looking for it, not stacked on top of the thing they
    # actually asked for.
    await status.edit_text(
        f"Отправлено — {megabytes:.1f} МБ, {report.width}×{report.height}, "
        f"{report.duration or 0} с.",
        reply_markup=_summary_keyboard(token, result),
    )


# One gather per map per chat, for as long as the process lives. Re-rendering the
# same replay at another size is the commonest thing anybody does with the Again
# button, and paying a minute of rate-limited lookups for an answer we had thirty
# seconds ago is the sort of cost nobody reports as a bug and everybody feels.
#
# The pictures live in their own directory rather than in the render's temporary
# one, because the cached rows name them by path: written beside a render, they
# would vanish with it and every re-render would draw a board of empty frames.
_scoreboards: dict[tuple[int, int], str] = {}


def _faces_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "dossier-faces")
    os.makedirs(path, exist_ok=True)
    return path


async def _player_pictures(
    verdict: dict, into: str, client=None
) -> tuple[str | None, str | None]:
    """The face of whoever *played* the replay, not whoever sent it.

    Looked up by the osu! name in the `.osr`. It was looked up by the sender's
    Telegram id, which is right exactly when somebody renders their own play and
    wrong every other time: throw a friend's replay at the bot and their row wore
    your face. The row belongs to the play, and the play names its own player.

    Nothing when the bot does not know them — an empty frame is honest, and
    somebody else's photograph is not.
    """
    name = (verdict.get("player") or "").strip()
    if not name:
        return (None, None)
    try:
        async with get_db_session() as session:
            user = (
                await session.execute(
                    select(User).where(func.lower(User.osu_username) == name.lower())
                )
            ).scalars().first()
            if not user:
                logger.info("no chat member is called %s — the row goes without a face", name)
                return (None, None)
            await dossier.ensure_pictures(client, session, [user])
            return dossier.pictures_for(user, into, str(user.osu_user_id or name))
    except Exception as exc:  # noqa: BLE001 — a missing face is not worth a render
        logger.debug("no pictures for %s: %s", name, exc)
        return (None, None)


async def _gather_rivals(verdict: dict, client, status=None, pictures_into=None) -> str:
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
                pictures_into,
                verdict.get("player"),
            )
    except Exception as exc:  # noqa: BLE001 — DB or API, and neither is worth a render
        logger.warning("could not build the scoreboard: %s", exc)
        return ""
    _scoreboards[(chat_id, beatmap_id)] = board
    return board


async def _why_no_scoreboard(verdict: dict) -> str:
    """Name the reason rather than leaving the left of the frame bare.

    An empty scoreboard has several quite different causes and they call for
    different responses — choose a chat, expect nothing, wait for someone to
    play it, render somebody who is actually here, or come and look at a bug.
    Drawing nothing and saying nothing makes all of them look like the last one.
    """
    if not verdict.get("chat_id"):
        return (
            "Скорборда нет: в личке бот не знает, чью беседу сравнивать. "
            "Пришли реплей в беседу или выбери её для лички."
        )
    status = (verdict.get("beatmap_status") or "").lower()
    if status and status not in ("ranked", "approved", "qualified", "loved"):
        return f"Скорборда нет: у карты статус {status}, у osu! на такие нет таблицы."
    player = (verdict.get("player") or "").strip()
    # Asked of the same function the gate uses, so the message and the decision
    # cannot drift apart — two answers to "is this person here" is one answer
    # and a future bug report nobody can reproduce.
    try:
        async with get_db_session() as session:
            here = await dossier.plays_here(session, verdict["chat_id"], player)
    except Exception as exc:  # noqa: BLE001 — a message is not worth a failure
        logger.debug("could not check whether %s is in the chat: %s", player, exc)
        here = True
    if not here:
        return (
            f"Скорборда нет: {player or 'этого игрока'} нет в беседе, "
            "а сравнивать чужой прогон не с кем."
        )
    return "Скорборда нет: ни у кого из беседы нет счёта на этой карте."


def _cancel_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✖️ Отменить", callback_data=f"dsx:{token}")]]
    )


@router.callback_query(F.data.startswith("dsx:"))
async def on_cancel(callback: types.CallbackQuery) -> None:
    """Call off a render in flight.

    The button has been drawn on every progress message since renders existed
    and nothing answered it: `dsx:` appeared once in the whole repository, on
    the keyboard. A tap did nothing at all — not even the spinner Telegram
    shows until a callback is answered — so the render ran to the end while the
    person who asked for it watched a dead button.

    Cancelling is worth having rather than politely ignoring: a render is
    minutes of one core, and the commonest reason to stop one is having asked
    for the wrong size.
    """
    pending = renders.get(callback.data.split(":", 1)[1])
    task = pending.task if pending else None
    if task is None or task.done():
        # Either the replay has been let go of, or the render finished between
        # the tap and this line. Both are "nothing to stop" from here, and both
        # are answered rather than left hanging.
        await callback.answer("Уже нечего отменять.")
        return
    task.cancel()
    # The waiting side edits the message to say so — `_render` catches
    # `CancelledError` and offers to try again. Answering here is only the
    # acknowledgement Telegram wants within a few seconds.
    await callback.answer("Отменяю…")


def _again_keyboard(token: str) -> InlineKeyboardMarkup:
    """After a render that did not produce a video. The replay is still here, so
    the next attempt costs a tap rather than another upload."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Ещё раз", callback_data=f"dsr:{token}")]
        ]
    )


def _summary_keyboard(token: str, result: dict | None = None) -> InlineKeyboardMarkup:
    """After a video. Where the engine's own read-outs live now.

    They used to sit on the result card, four of them, offered to everybody
    every time — and on a card that says the result plainly they were four taps
    of noise around the two that matter. They belong to somebody who has
    watched a render and wants to know why it says what it says, which is
    exactly who is looking at this message.
    """
    rows = [
        [InlineKeyboardButton(text="📋 Итоги рендера", callback_data=f"dsm:{token}")],
    ]
    available = [
        InlineKeyboardButton(text=label, callback_data=f"dsa:{token}:{key}")
        for key, label in _SECTIONS
        if result and _section_text(key, result).strip()
    ]
    # Two to a row: four full-width buttons push the last one off the first
    # screen on a phone.
    for at in range(0, len(available), 2):
        rows.append(available[at : at + 2])
    rows.append([InlineKeyboardButton(text="🎬 Ещё раз", callback_data=f"dsr:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        # A reel is several renders in a row, so the bar fills once per clip.
        # Without saying which clip, that reads as a render starting over —
        # five times.
        which = f" · клип {progress.clip[0]}/{progress.clip[1]}" if progress.clip else ""
        try:
            await status.edit_text(
                f"Рендерю {size}{which}\n"
                f"<code>{bar}</code> {progress.fraction * 100:.0f}%\n"
                f"{progress.done}/{progress.total} кадров · {progress.fps:.0f}/с · "
                f"осталось ~{_left(progress.seconds_left)}",
                parse_mode="HTML",
            )
        except Exception as exc:  # noqa: BLE001 — a failed edit must not stop a render
            logger.debug("progress edit failed: %s", exc)

    return watch


# Telegram allows a thousand characters under a video and cuts the rest without
# saying so. Five moments and a title come to about four hundred; the cap is
# here so a longer reel loses its last line rather than its whole caption.
_CAPTION_LIMIT = 1000


def _caption(title: str, selection) -> str:
    """The title, and — for a reel — what the engine chose and why.

    The reasons go with the video rather than behind a button. They are the
    whole claim the feature makes: without them a reel is a minute somebody has
    to take on trust, and with them it is a minute somebody can disagree with.
    """
    if selection is None or not selection.clips:
        return title
    lines = [title, ""]
    for moment in selection.clips:
        lines.append(f"{moment.stamp()} — {moment.say()}")
        # Indented under the moment it shares its seconds with, so it reads as
        # one clip saying two things rather than as two clips.
        if moment.also:
            lines.append(f"        · {moment.also.say()}")
    text = "\n".join(lines)
    while len(text) > _CAPTION_LIMIT and len(lines) > 2:
        lines.pop()
        text = "\n".join(lines)
    return text


def _escape(lines: list[str]) -> str:
    """Whatever the engine says lands inside a <pre>, so its angle brackets
    have to stop being markup."""
    text = "\n".join(lines) or "(движок ничего не сообщил)"
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
