import asyncio
import os
import tempfile
from time import monotonic

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.filters.text_trigger import TextTriggerFilter
from bot.handlers.dossier import renders
from db.database import get_db_session
from db.models.user import User
from utils.osu.resolve_user import get_registered_user
from sqlalchemy import func, select
from config.settings import ADMIN_IDS, MAX_SKIN_MB, TELEGRAM_BOT_API_URL
from services import dossier
from dossier import build as dossier_build
from services.dossier import shared
from dossier import skins
from services.render_farm import dispatch as render_farm
from services.render_farm.queue import queue as render_queue
from services.render_farm import invites
from services.render_farm.roster import roster as render_roster
from utils.formatting.text import escape_html, plural as _plural
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="dossier")

_MAX_REPLAY_BYTES = 8 * 1024 * 1024

def _max_incoming_bytes() -> int:
    if TELEGRAM_BOT_API_URL:
        return 2000 * 1024 * 1024
    return 20 * 1024 * 1024

def _max_skin_bytes() -> int:
    return min(MAX_SKIN_MB * 1024 * 1024, _max_incoming_bytes())

def _max_video_bytes() -> int:
    if TELEGRAM_BOT_API_URL:
        return 2000 * 1024 * 1024
    return 48 * 1024 * 1024

def _count_word(lang: str, count: int, english: str,
                one: str, few: str, many: str) -> str:
    if lang == "ru":
        return _plural(count, one, few, many)
    return english if count == 1 else f"{english}s"

async def _keep_if_shared(telegram_id: int, tenant_chat_id, pending) -> None:
    if not shared.enabled():
        return
    try:
        async with get_db_session() as session:
            user = await get_registered_user(session, telegram_id, tenant_chat_id)
            if not user or not user.share_replays:
                return
    except Exception as exc:
        logger.warning("could not read the sharing preference: %s", exc)
        return

    await asyncio.to_thread(shared.keep, pending.replay_path, pending.verdict)

async def _lang(user) -> str:
    if user is None:
        return "en"
    return (await get_language(user.id)).lower()

@router.message(Command("dossier"))
async def on_status(message: types.Message) -> None:
    lang = await _lang(message.from_user)
    if not dossier.is_available():
        await message.reply(
            t("dsr.not_built", lang)

            + "<code>./venv/bin/python scripts/engine.py</code>",
            parse_mode="HTML",
        )
        return
    await message.reply(t("dsr.ready", lang), parse_mode="HTML")

@router.message(F.document)
async def on_replay_document(
    message: types.Message, osu_api_client=None, tenant_chat_id=None
) -> None:
    lang = await _lang(message.from_user)
    document = message.document
    name = (document.file_name or "").lower()
    if name.endswith(".osk"):
        await _take_skin(message, document, lang)
        return
    if not name.endswith(".osr"):
        return
    if document.file_size and document.file_size > _MAX_REPLAY_BYTES:
        await message.reply(t("dsr.too_big", lang))
        return

    status = await message.reply(t("dsr.reading", lang))

    with tempfile.TemporaryDirectory(prefix="dossier-") as workdir:
        replay_path = os.path.join(workdir, "replay.osr")
        try:
            await message.bot.download(document, destination=replay_path)
        except Exception as exc:
            logger.warning("replay download failed: %s", exc)
            await status.edit_text(t("dsr.download_failed", lang, why=exc))
            return

        try:
            header = await dossier.inspect(replay_path)
        except dossier.DossierError as exc:
            await status.edit_text(str(exc))
            return

        if "error" in header:
            await status.edit_text(t("dsr.unreadable", lang, why=header["error"]))
            return
        if header.get("mode") != "Standard":
            await status.edit_text(
                t("dsr.wrong_mode", lang, mode=header.get("mode", "?"))
            )
            return
        if not header.get("frames"):
            await status.edit_text(t("dsr.no_frames", lang))
            return

        await status.edit_text(
            t(
                "dsr.finding_map",
                lang,
                player=header["player"],
                mods=header["mods"],
                frames=header["frames"],
            )
        )

        try:
            beatmap = await dossier.ensure_map(osu_api_client, header["beatmap_hash"])
        except dossier.MapUnavailable as exc:
            await status.edit_text(str(exc))
            return

        await status.edit_text(t("dsr.judging", lang, map=dossier.describe(beatmap)))

        try:
            result = await dossier.judge(replay_path, dossier.songs_dir())
        except dossier.DossierError as exc:
            await status.edit_text(str(exc))
            return

        if "error" in result:
            await status.edit_text(t("dsr.judge_failed", lang, why=result["error"]))
            return

        result["api_max_combo"] = (beatmap or {}).get("max_combo")

        result["beatmap_id"] = (beatmap or {}).get("id")

        result["beatmapset_id"] = (beatmap or {}).get("beatmapset_id")

        result["chat_id"] = tenant_chat_id
        result["beatmap_status"] = (beatmap or {}).get("status")

        result["lazer"] = str(result.get("client", "")).startswith("lazer")
        result["no_audio"] = bool((beatmap or {}).get("_no_audio"))
        token = renders.remember(replay_path, dossier.describe(beatmap), result)

    await _answer_with_card(
        message, status, result, beatmap, token, osu_api_client, lang
    )

async def _answer_with_card(
    message, status, result: dict, beatmap, token: str, osu_api_client=None, lang: str = "en"
) -> None:
    keyboard = _verdict_keyboard(token, result, lang)
    try:
        photo = await _result_card(result, beatmap, message, osu_api_client)
    except Exception as exc:
        logger.warning("result card failed, falling back to the table: %s", exc)
        photo = None

    if photo is None:
        await status.edit_text(
            _format(result, dossier.describe(beatmap)),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    try:
        await status.delete()
    except Exception:
        pass
    await message.answer_photo(photo=photo, reply_markup=keyboard)

async def _result_card(result: dict, beatmap, message, osu_api_client=None):
    from aiogram.types import BufferedInputFile

    from services.dossier.card import score_from_replay
    from services.image import card_renderer
    from services.image.render.recent import build_recent_card_data

    if not beatmap:
        return None
    player, cover = await _who_played(result, osu_api_client)
    score = score_from_replay(result, beatmap)

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
    name = (result.get("player") or "").strip()
    if not name or osu_api_client is None:
        return 0, ""
    try:
        found = await osu_api_client.get_user_data(name)
    except Exception as exc:
        logger.debug("could not look up %s: %s", name, exc)
        return 0, ""
    if not found:
        return 0, ""
    return int(found.get("id") or 0), found.get("cover_url") or ""

async def _assay(result: dict, score: dict):
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

async def _take_skin(message: types.Message, document, lang: str = "en") -> None:
    if document.file_size and document.file_size > _max_skin_bytes():

        megabytes = _max_skin_bytes() // 1024 // 1024
        if not TELEGRAM_BOT_API_URL and MAX_SKIN_MB * 1024 * 1024 > _max_incoming_bytes():
            await message.reply(t("dsr.skin_too_big_telegram", lang, mb=megabytes))
        else:
            await message.reply(t("dsr.skin_too_big", lang, mb=megabytes))
        return

    status = await message.reply(t("dsr.skin_taking", lang))
    with tempfile.TemporaryDirectory(prefix="dossier-skin-") as workdir:
        archive = os.path.join(workdir, "skin.osk")
        try:
            await message.bot.download(document, destination=archive)
        except Exception as exc:
            logger.warning("skin download failed: %s", exc)
            await status.edit_text(t("dsr.download_failed", lang, why=exc))
            return

        try:
            name = await asyncio.to_thread(
                skins.import_osk,
                archive,
                document.file_name or "skin.osk",
                message.from_user.id if message.from_user else None,
            )
        except skins.SkinRejected as exc:
            await status.edit_text(t("dsr.skin_refused", lang, why=exc))
            return

    count = len(os.listdir(skins.folder_of(name) or "."))
    await status.edit_text(
        t(
            "dsr.skin_stored",
            lang,
            name=escape_html(name),
            count=count,

            word=_count_word(lang, count, "file", "файл", "файла", "файлов"),
        ),
        parse_mode="HTML",
    )

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

    return f"{header}\n<pre>{chr(10).join(lines)}</pre>{verdict}{_explain_early_end(result)}"

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

def _verdict_keyboard(token: str, result: dict, lang: str = "en") -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=t("dsr.kb.render", lang), callback_data=f"dsr:{token}"),
            InlineKeyboardButton(text=t("dsr.kb.reel", lang), callback_data=f"dse:{token}"),
        ]
    ]
    beatmap_id = result.get("beatmap_id")
    beside = []
    if beatmap_id:
        beside.append(
            InlineKeyboardButton(
                text=t("dsr.kb.map", lang),
                url=f"https://osu.ppy.sh/beatmaps/{beatmap_id}",
            )
        )
        beside.append(
            InlineKeyboardButton(text=t("dsr.kb.board", lang), callback_data=f"lbm:{beatmap_id}")
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
    off = result.get("score_error")
    if off is None:
        return ""
    if abs(off) < 0.05:
        return f"\n\nОчки сходятся ({off:+.2f}%)."
    return f"\n\nОчки расходятся на {off:+.2f}%."

def _compare_combo_ceiling(result: dict, api_max_combo: int | None) -> str:
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

@router.callback_query(F.data.startswith("dsr:"))
async def on_render(
    callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None
) -> None:
    await _render(callback, osu_api_client, reel=False, tenant_chat_id=tenant_chat_id)

@router.callback_query(F.data.startswith("dse:"))
async def on_exhibit(
    callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None
) -> None:
    await _render(callback, osu_api_client, reel=True, tenant_chat_id=tenant_chat_id)

async def _render(
    callback: types.CallbackQuery, osu_api_client, *, reel: bool, tenant_chat_id=None
) -> None:
    token = callback.data.split(":", 1)[1]
    if renders.is_rendering(token):
        await callback.answer(t("dsr.busy", await _lang(callback.from_user)),
                              show_alert=True)
        return
    with renders.one_at_a_time(token):
        await _render_now(callback, osu_api_client, reel=reel,
                          tenant_chat_id=tenant_chat_id)

async def _render_now(
    callback: types.CallbackQuery, osu_api_client, *, reel: bool, tenant_chat_id=None
) -> None:
    lang = await _lang(callback.from_user)
    token = callback.data.split(":", 1)[1]
    pending = renders.get(token)
    if not pending:
        await callback.answer(t("dsr.gone", lang), show_alert=True)
        return

    choices = renders.choices(callback.from_user.id)
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        renders.restore_settings(user, choices)

        if choices.heavy():
            if renders.heavy_left(user) <= 0:
                await callback.answer(
                    t("dsr.ration_spent", lang, total=renders.HEAVY_PER_DAY),
                    show_alert=True,
                )
                return
            renders.spend_heavy(user)
            await session.commit()
    size = choices.size
    await callback.answer()

    status = await callback.message.answer(
        t("dsr.board_building", lang), reply_markup=_cancel_keyboard(token, lang)
    )
    faces = _faces_dir()
    rivals = await _gather_rivals(pending.verdict, osu_api_client, status, faces, lang)
    mine = await _player_pictures(pending.verdict, faces, osu_api_client)
    warning = ""
    if pending.verdict.get("no_audio"):
        warning += (
            t("dsr.no_audio", lang)
        )
    if not rivals:
        warning += "\nℹ️ " + await _why_no_scoreboard(pending.verdict, lang)

    selection = None

    line = t("dsr.rendering", lang, what=choices.summary(lang))
    if reel:
        try:
            selection = await dossier.moments(pending.replay_path, dossier.songs_dir())
        except dossier.DossierError as exc:
            await status.edit_text(
                t("dsr.reel_failed", lang, why=_escape(str(exc).splitlines())),
                parse_mode="HTML",
                reply_markup=_again_keyboard(token),
            )
            return
        found = len(selection.clips)
        line = t(
            "dsr.reel_chose",
            lang,
            found=found,
            word=_count_word(lang, found, "moment", "момент", "момента", "моментов"),
            seconds=selection.watch_seconds(),
        )
    await status.edit_text(f"{line}{warning}", reply_markup=_cancel_keyboard(token, lang))
    out_path = os.path.join(pending.workdir, "reel.mp4" if reel else "replay.mp4")

    chosen_skin = skins.folder_of(choices.skin) if choices.skin else None
    if choices.skin and skins.is_stale(choices.skin):

        logger.warning(
            "skin %s was unpacked by an older importer; it should be sent again",
            choices.skin,
        )
    common = dict(
        size=size,
        fps=choices.fps,
        mute=choices.mute,
        skin=chosen_skin,

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
    keys = _cancel_keyboard(token, lang)
    watch = _progress_watcher(status, size, lang, keys)

    async def in_line(waiting) -> None:
        if not waiting.workers:
            said = t("dsr.nobody_here", lang)
        elif waiting.ahead:
            said = t("dsr.in_line", lang, ahead=waiting.ahead,
                     word=_count_word(lang, waiting.ahead,
                                      "render", "рендер", "рендера", "рендеров"))
        else:
            said = t("dsr.all_busy", lang, workers=waiting.workers,
                     word=_count_word(lang, waiting.workers,
                                      "machine", "компьютер", "компьютера", "компьютеров"))
        try:
            await status.edit_text(said, reply_markup=keys)
        except Exception as exc:
            logger.debug("could not say the queue position: %s", exc)

    common["title"] = pending.title

    common["beatmap"] = {
        "id": (pending.verdict or {}).get("beatmap_id"),
        "beatmapset_id": (pending.verdict or {}).get("beatmapset_id"),
    }
    if reel:
        common["chosen"] = selection
    engine = render_farm.exhibit if reel else render_farm.video
    pending.task = asyncio.create_task(
        engine(
            pending.replay_path,
            dossier.songs_dir(),
            out_path,
            on_progress=watch,
            on_queue=in_line,
            **common,
        )
    )
    try:
        report = await pending.task
        if reel:

            report, selection = report.render, report.selection
    except asyncio.CancelledError:
        await status.edit_text(
            t("dsr.cancelled", lang), reply_markup=_again_keyboard(token, lang)
        )
        return
    except dossier.DossierError as exc:
        await status.edit_text(
            t("dsr.failed", lang, why=_escape(str(exc).splitlines())),
            parse_mode="HTML",
            reply_markup=_again_keyboard(token, lang),
        )
        return
    finally:
        pending.task = None

    pending.report = report.report

    await _keep_if_shared(callback.from_user.id, tenant_chat_id, pending)
    size_bytes = os.path.getsize(out_path)
    megabytes = size_bytes / 1024 / 1024
    if size_bytes > _max_video_bytes():
        await status.edit_text(
            t("dsr.too_big_to_send", lang, mb=megabytes, path=out_path),
            parse_mode="HTML",
            reply_markup=_summary_keyboard(token, pending.verdict, lang),
        )
        return

    await status.edit_text(t("dsr.sending", lang, mb=megabytes, size=size))
    try:
        await callback.message.answer_video(
            types.FSInputFile(out_path),
            caption=_caption(pending.title, selection),
            supports_streaming=True,

            width=report.width,
            height=report.height,
            duration=report.duration,
        )
    except Exception as exc:
        logger.warning("video upload failed: %s", exc)

        await status.edit_text(
            t("dsr.send_failed", lang, mb=megabytes, why=exc, path=out_path),
            parse_mode="HTML",
            reply_markup=_summary_keyboard(token, pending.verdict, lang),
        )
        return

    await status.edit_text(
        t(
            "dsr.sent",
            lang,
            mb=megabytes,
            width=report.width,
            height=report.height,
            seconds=report.duration or 0,
        ),
        reply_markup=_summary_keyboard(token, pending.verdict, lang),
    )

_scoreboards: dict[tuple[int, int], str] = {}

def _faces_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "dossier-faces")
    os.makedirs(path, exist_ok=True)
    return path

async def _player_pictures(
    verdict: dict, into: str, client=None
) -> tuple[str | None, str | None]:
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
    except Exception as exc:
        logger.debug("no pictures for %s: %s", name, exc)
        return (None, None)

async def _gather_rivals(
    verdict: dict, client, status=None, pictures_into=None, lang: str = "en"
) -> str:
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

        if now - last < 3.0 or status is None:
            return
        last = now
        try:
            await status.edit_text(t("dsr.board_progress", lang, done=done, total=total))
        except Exception as exc:
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
    except Exception as exc:
        logger.warning("could not build the scoreboard: %s", exc)
        return ""
    _scoreboards[(chat_id, beatmap_id)] = board
    return board

async def _why_no_scoreboard(verdict: dict, lang: str = "en") -> str:
    if not verdict.get("chat_id"):
        return t("dsr.board_dm", lang)
    status = (verdict.get("beatmap_status") or "").lower()
    if status and status not in ("ranked", "approved", "qualified", "loved"):
        return t("dsr.board_status", lang, status=status)
    player = (verdict.get("player") or "").strip()

    try:
        async with get_db_session() as session:
            here = await dossier.plays_here(session, verdict["chat_id"], player)
    except Exception as exc:
        logger.debug("could not check whether %s is in the chat: %s", player, exc)
        here = True
    if not here:
        return t(
            "dsr.board_stranger",
            lang,
            player=player or t("dsr.board_that_player", lang),
        )
    return t("dsr.board_empty", lang)

def _cancel_keyboard(token: str, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("dsr.cancel", lang), callback_data=f"dsx:{token}")]
        ]
    )

@router.callback_query(F.data.startswith("dsx:"))
async def on_cancel(callback: types.CallbackQuery) -> None:
    lang = await _lang(callback.from_user)
    pending = renders.get(callback.data.split(":", 1)[1])
    task = pending.task if pending else None
    if task is None or task.done():

        await callback.answer(t("dsr.nothing_to_cancel", lang))
        return
    task.cancel()

    await callback.answer(t("dsr.cancelling", lang))

def _again_keyboard(token: str, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("dsr.again", lang), callback_data=f"dsr:{token}")]
        ]
    )

def _summary_keyboard(
    token: str, result: dict | None = None, lang: str = "en"
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t("dsr.summary", lang), callback_data=f"dsm:{token}")],
    ]
    available = [
        InlineKeyboardButton(text=label, callback_data=f"dsa:{token}:{key}")
        for key, label in _SECTIONS
        if result and _section_text(key, result).strip()
    ]

    for at in range(0, len(available), 2):
        rows.append(available[at : at + 2])
    rows.append(
        [InlineKeyboardButton(text=t("dsr.again", lang), callback_data=f"dsr:{token}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data.startswith("dsm:"))
async def on_summary(callback: types.CallbackQuery) -> None:
    lang = await _lang(callback.from_user)
    pending = renders.get(callback.data.split(":", 1)[1])
    if not pending or not pending.report:
        await callback.answer(t("dsr.summary_gone", lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"<pre>{_escape(pending.report)}</pre>", parse_mode="HTML"
    )

_PROGRESS_EVERY_SECONDS = 8.0

def _left(seconds: float, lang: str = "en") -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return t("dsr.seconds", lang, seconds=seconds)
    return t("dsr.minutes", lang, minutes=seconds // 60, seconds=seconds % 60)

def _progress_watcher(status: types.Message, size: str, lang: str = "en",
                      keyboard=None):
    last = 0.0

    async def watch(progress) -> None:
        nonlocal last
        now = monotonic()
        if now - last < _PROGRESS_EVERY_SECONDS:
            return
        last = now
        filled = round(progress.fraction * 12)
        bar = "█" * filled + "░" * (12 - filled)

        which = (
            t("dsr.progress_clip", lang, at=progress.clip[0], of=progress.clip[1])
            if progress.clip
            else ""
        )
        try:
            await status.edit_text(
                t(
                    "dsr.progress",
                    lang,
                    size=size,
                    which=which,
                    done=progress.done,
                    total=progress.total,
                    fps=progress.fps,
                    left=_left(progress.seconds_left, lang),
                ).replace(
                    "\n", f"\n<code>{bar}</code> {progress.fraction * 100:.0f}%\n", 1
                ),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.debug("progress edit failed: %s", exc)

    return watch

_CAPTION_LIMIT = 1000

def _caption(title: str, selection) -> str:
    if selection is None or not selection.clips:
        return title
    lines = [title, ""]
    for moment in selection.clips:
        lines.append(f"{moment.stamp()} — {moment.say()}")

        if moment.also:
            lines.append(f"        · {moment.also.say()}")
    text = "\n".join(lines)
    while len(text) > _CAPTION_LIMIT and len(lines) > 2:
        lines.pop()
        text = "\n".join(lines)
    return text

def _escape(lines: list[str]) -> str:
    text = "\n".join(lines) or "(движок ничего не сообщил)"
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

@router.message(TextTriggerFilter("cltoken"))
async def on_cltoken(message: types.Message, lang: str = "en", **_) -> None:
    who = message.from_user
    if not who or who.id not in ADMIN_IDS:
        await message.reply(t("dsr.cltoken.not_yours", lang))
        return

    code = invites.pretty(invites.offer(who.id, who.full_name or ""))
    said = t("dsr.cltoken.here", lang, code=code,
             minutes=int(invites.GOOD_FOR // 60))
    try:
        await message.bot.send_message(who.id, said, parse_mode="HTML")
    except Exception as exc:
        logger.warning("could not send a farm code privately: %s", exc)
        await message.reply(t("dsr.cltoken.no_dm", lang), parse_mode="HTML")
        return

    if message.chat.type != "private":
        await message.reply(t("dsr.cltoken.sent_privately", lang))

@router.message(TextTriggerFilter("rdrw"))
async def on_farm(message: types.Message, lang: str = "en", **_) -> None:
    workers = render_roster.here()
    if not workers:
        await message.reply(t("dsr.farm.empty", lang), parse_mode="HTML")
        return

    busy = render_queue.rendering()
    ours = await dossier_build.local()
    lines = [t(
        "dsr.farm.head", lang,
        workers=len(workers),
        word=_count_word(lang, len(workers), "machine",
                         "машина", "машины", "машин"),
        waiting=len(render_queue.waiting()),
    )]

    for worker in workers:
        state = worker.state(rendering=worker.name in busy)
        parts = [t(f"dsr.farm.{state}", lang)]
        if worker.threads:
            parts.append(t("dsr.farm.threads", lang, threads=worker.threads))
        if worker.reason and state == "resting":
            parts.append(escape_html(worker.reason))
        lines.append(f"\n▸ <b>{escape_html(worker.name)}</b> — {' · '.join(parts)}")

        if worker.delivered or worker.handed_back:
            lines.append("  " + t("dsr.farm.tally", lang,
                                  delivered=worker.delivered,
                                  back=worker.handed_back))

        theirs = dossier_build.build_of(worker.build)
        mine = dossier_build.build_of(ours)
        if worker.build and theirs != mine and dossier_build.UNKNOWN not in (theirs, mine):
            lines.append("  " + t("dsr.farm.stale_build", lang,
                                  build=theirs, ours=mine))

    await message.reply("\n".join(lines), parse_mode="HTML")
