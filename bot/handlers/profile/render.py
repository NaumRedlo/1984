import os
import time
import hashlib
import tempfile
from typing import Optional, Dict

from aiogram import Router, F, types
from aiogram.types import BufferedInputFile, FSInputFile
from osrparse import Replay
from sqlalchemy import select

from config.settings import RENDER_MAX_VIDEO_MB, RENDER_WORKER_URL
from db.database import get_db_session
from db.models.user import User
from db.models.render_settings import UserRenderSettings
from db.models.render_cache import RenderCache
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.osu.api_client import OsuApiClient
from utils.osu.resolve_user import resolve_osu_user, get_registered_user
from utils.osu.helpers import get_message_context
from utils.osu import danser_renderer
from utils.osu import render_client
from utils.cloud import gpu_power
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.dm_tenant import ensure_dm_tenant

logger = get_logger("handlers.render")
router = Router(name="render")

# Cooldown: tg_id -> last render timestamp
_cooldowns: Dict[int, float] = {}
COOLDOWN_SECONDS = 60

# Max video size to send. 50 MB on the cloud Bot API; ~2 GB with a local Bot
# API server (config-driven, see RENDER_MAX_VIDEO_MB / TELEGRAM_BOT_API_URL).
MAX_VIDEO_BYTES = RENDER_MAX_VIDEO_MB * 1024 * 1024


def _check_cooldown(tg_id: int) -> Optional[int]:
    """Returns remaining cooldown seconds, or None if ready."""
    last = _cooldowns.get(tg_id)
    if last is None:
        return None
    elapsed = time.time() - last
    if elapsed >= COOLDOWN_SECONDS:
        return None
    return int(COOLDOWN_SECONDS - elapsed)


async def _get_or_create_settings(session, user_id: int) -> UserRenderSettings:
    """Get user render settings from DB, or return defaults."""
    stmt = select(UserRenderSettings).where(UserRenderSettings.user_id == user_id)
    result = await session.execute(stmt)
    settings = result.scalar_one_or_none()
    if settings:
        return settings
    settings = UserRenderSettings(user_id=user_id)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


def _settings_to_dict(settings: UserRenderSettings) -> dict:
    """Convert DB settings to a plain dict for danser_renderer."""
    return {
        "skin": settings.skin,
        "resolution": settings.resolution,
        "cursor_size": settings.cursor_size,
        "cursor_trail": settings.cursor_trail,
        "show_pp_counter": settings.show_pp_counter,
        "show_scoreboard": settings.show_scoreboard,
        "show_key_overlay": settings.show_key_overlay,
        "show_hit_error_meter": settings.show_hit_error_meter,
        "show_mods": settings.show_mods,
        "show_result_screen": settings.show_result_screen,
        "show_strain_graph": settings.show_strain_graph,
        "show_hit_counter": settings.show_hit_counter,
        "show_seizure_warning": settings.show_seizure_warning,
        "bg_dim": settings.bg_dim,
    }


async def _do_render(osr_path, beatmapset_id, render_settings, out_name, on_progress, on_queue):
    """Render a replay, locally or on the remote worker depending on
    RENDER_WORKER_URL. Returns (video_path, width, height, duration); video_path
    is a temp file the caller must delete. Raises the same danser_renderer
    exceptions in both modes (plus RenderWorkerUnreachable in remote mode)."""
    if RENDER_WORKER_URL:
        # gpu_power.session wakes the on-demand GPU server (and powers it off when
        # no renders remain) — a no-op unless RENDER_AUTOPOWER is set. on_wake shows
        # the boot stage; once up we show the render stage.
        async with gpu_power.session(on_wake=on_progress):
            if on_progress:
                try:
                    await on_progress("Рендеринг видео на GPU...")
                except Exception:
                    pass
            with open(osr_path, "rb") as f:
                osr_bytes = f.read()
            return await render_client.render_remote(osr_bytes, beatmapset_id, render_settings)

    video_path = await danser_renderer.render_replay(
        replay_path=osr_path,
        output_path=out_name,
        settings=render_settings,
        on_progress=on_progress,
        on_queue=on_queue,
    )
    w, h, dur = await danser_renderer.probe_video(video_path)
    return video_path, w, h, dur


# Bump when the render pipeline changes the output bytes (resolution/fps/encoder)
# so stale cached file_ids aren't reused. Cache is also a quick admin-purge target.
RENDER_PIPELINE_VERSION = "1"


_SIG_FIELDS = (
    "resolution", "bg_dim", "cursor_size",
    "show_pp_counter", "show_scoreboard", "show_key_overlay",
    "show_hit_error_meter", "show_mods", "show_result_screen",
    "show_strain_graph", "show_hit_counter", "show_seizure_warning",
)


def _settings_sig(render_settings: Optional[Dict]) -> str:
    """Short signature of the settings that affect the rendered output, so two
    different setups (resolution, HUD toggles, dim, cursor) don't collide in the
    cache."""
    if not render_settings:
        return "def"
    raw = "|".join(f"{k}={render_settings.get(k)}" for k in _SIG_FIELDS)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _cache_key(source: str, render_settings: Optional[Dict]) -> str:
    return f"{source}:{_settings_sig(render_settings)}:v{RENDER_PIPELINE_VERSION}"


async def _cache_lookup(key: str) -> Optional[str]:
    async with get_db_session() as session:
        row = (await session.execute(
            select(RenderCache).where(RenderCache.cache_key == key)
        )).scalar_one_or_none()
        return row.file_id if row else None


async def _cache_store(key: str, file_id: str) -> None:
    async with get_db_session() as session:
        existing = (await session.execute(
            select(RenderCache).where(RenderCache.cache_key == key)
        )).scalar_one_or_none()
        if existing:
            existing.file_id = file_id
        else:
            session.add(RenderCache(cache_key=key, file_id=file_id))
        await session.commit()


# ── render ──

@router.message(TextTriggerFilter("render"))
async def cmd_render(message: types.Message, trigger_args: TriggerArgs, osu_api_client, tenant_chat_id=None):
    tg_id = message.from_user.id

    # Cooldown check
    remaining = _check_cooldown(tg_id)
    if remaining:
        await message.answer(f"Подождите <b>{remaining} сек.</b> перед следующим рендером.", parse_mode="HTML")
        return

    user_input = (trigger_args.args or "").strip() if trigger_args else ""
    score_id = None
    beatmapset_id = None
    display_name = ""

    # Get requester's OAuth token for API calls
    requester_token = None
    async with get_db_session() as session:
        req_user = await get_registered_user(session, tg_id, tenant_chat_id)
        if req_user:
            requester_token = await OsuApiClient.try_get_oauth_token(req_user.telegram_id)

    if user_input:
        # Resolve user, fetch their latest score
        wait_msg = await message.answer(f"Поиск игрока <b>{escape_html(user_input)}</b>...", parse_mode="HTML")
        try:
            user_data = await resolve_osu_user(osu_api_client, user_input)
            if not user_data:
                await wait_msg.edit_text(f"Игрок <b>{escape_html(user_input)}</b> не найден.", parse_mode="HTML")
                return
            target_id = user_data.get("id")
            display_name = user_data.get("username", user_input)
            recent = await osu_api_client.get_user_recent_scores(target_id, limit=1, oauth_token=requester_token)
            if not recent:
                await wait_msg.edit_text(f"У <b>{escape_html(display_name)}</b> нет недавних игр.", parse_mode="HTML")
                return
            score_id = recent[0].get("id")
            beatmapset_id = recent[0].get("beatmapset", {}).get("id") or recent[0].get("beatmap", {}).get("beatmapset_id")
        except Exception as e:
            logger.error(f"Error resolving user for render: {e}")
            await wait_msg.edit_text("Ошибка при поиске игрока.", parse_mode="HTML")
            return
    else:
        # Try to get score_id from recent card context
        ctx = get_message_context(message.chat.id, message.message_id)
        if ctx and ctx.get("score_id"):
            score_id = ctx["score_id"]
            beatmapset_id = ctx.get("beatmapset_id")
            display_name = ctx.get("username", "")
        else:
            await message.answer(
                "Нет контекста для рендера.\n"
                "Сначала используйте <code>rs</code>, укажите ник: <code>render [никнейм]</code>,\n"
                "или пришлите <code>.osr</code>-файл с подписью <code>render</code>.",
                parse_mode="HTML",
            )
            return

    if not score_id:
        await message.answer("Не удалось определить скор для рендера.")
        return

    # Status message
    if not user_input:
        wait_msg = await message.answer(
            f"Подготовка рендера <b>{escape_html(display_name)}</b>...",
            parse_mode="HTML",
        )

    # Load render settings up front — needed for the cache key.
    render_settings = None
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        if user:
            settings = await _get_or_create_settings(session, user.id)
            render_settings = _settings_to_dict(settings)

    # Cache: same score + settings already rendered? Re-send the stored file_id
    # instantly — no GPU wake, no danser render.
    cache_key = _cache_key(f"score:{score_id}", render_settings)
    cached_file_id = await _cache_lookup(cache_key)
    if cached_file_id:
        try:
            await wait_msg.delete()
        except Exception:
            pass
        try:
            await message.answer_video(video=cached_file_id, supports_streaming=True)
            _cooldowns[tg_id] = time.time()
            return
        except Exception as e:
            # Stale file_id (rare) — fall through to a fresh render.
            logger.info(f"cached file_id failed, re-rendering: {e}")

    # Check danser availability (local mode only — in remote mode danser and the
    # beatmap download live on the worker, and the bot has no danser binary).
    if not RENDER_WORKER_URL:
        try:
            danser_renderer._check_danser()
        except danser_renderer.DanserNotFoundError as e:
            await wait_msg.edit_text(str(e), parse_mode="HTML")
            return

        # Download beatmap if needed
        if beatmapset_id:
            await wait_msg.edit_text("Загрузка карты...", parse_mode="HTML")
            map_ok = await danser_renderer.download_beatmap(beatmapset_id)
            if not map_ok:
                await wait_msg.edit_text("Не удалось скачать карту. Попробуйте позже.")
                return

    # Download replay
    await wait_msg.edit_text("Загрузка реплея...", parse_mode="HTML")

    tmp_dir = tempfile.mkdtemp(prefix="render_")
    video_path = None

    try:
        osr_path = await danser_renderer.download_replay_file(osu_api_client, score_id, tmp_dir)
        if not osr_path:
            await wait_msg.edit_text(
                "Реплей недоступен для этого скора.\n"
                "Возможно, реплей не был сохранён (фейл или старый скор).",
                parse_mode="HTML",
            )
            return

        # Render
        if RENDER_WORKER_URL:
            await wait_msg.edit_text("Рендеринг на удалённом сервере...", parse_mode="HTML")
        else:
            await wait_msg.edit_text("Рендеринг видео...", parse_mode="HTML")

        async def on_progress(text: str):
            try:
                await wait_msg.edit_text(text, parse_mode="HTML")
            except Exception:
                pass

        async def on_queue(position: int):
            try:
                await wait_msg.edit_text(
                    f"В очереди на рендер: <b>#{position}</b>. Ожидайте...",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        out_name = f"render_{score_id}_{int(time.time())}"

        try:
            video_path, w, h, dur = await _do_render(
                osr_path, beatmapset_id, render_settings, out_name, on_progress, on_queue,
            )
        except danser_renderer.RenderQueueFullError:
            await wait_msg.edit_text("Слишком много рендеров в очереди. Попробуйте позже.")
            return
        except render_client.RenderWorkerUnreachable:
            await wait_msg.edit_text("Сервер рендеринга недоступен. Попробуйте позже.")
            return
        except danser_renderer.DanserError as e:
            await wait_msg.edit_text(f"Ошибка рендеринга: {escape_html(str(e))}", parse_mode="HTML")
            return

        # Send video
        _cooldowns[tg_id] = time.time()
        file_size = os.path.getsize(video_path)

        if file_size <= MAX_VIDEO_BYTES:
            await wait_msg.edit_text("Отправка видео...", parse_mode="HTML")
            try:
                video_file = FSInputFile(video_path, filename="render.mp4")
                await wait_msg.delete()
                sent = await message.answer_video(
                    video=video_file, width=w, height=h, duration=dur,
                    supports_streaming=True,
                )
                if sent and sent.video:
                    await _cache_store(cache_key, sent.video.file_id)
            except Exception as e:
                logger.error(f"Failed to send video: {e}")
                await message.answer("Не удалось отправить видео в Telegram.")
        else:
            await wait_msg.edit_text(
                f"Видео слишком большое для Telegram ({file_size // (1024*1024)} МБ).",
                parse_mode="HTML",
            )

    finally:
        # Cleanup temp files
        try:
            if os.path.isdir(tmp_dir):
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        # Cleanup rendered video
        if video_path and os.path.isfile(video_path):
            try:
                os.remove(video_path)
            except Exception:
                pass


# ── render from .osr file ──

# A bare `.osr` upload belongs to the bounty replay-verify flow
# (bot/handlers/bounty/replay.py). The profile router is included BEFORE the
# bounty router, so render only claims an uploaded replay when the caption
# explicitly asks for it ("render"); otherwise the filter fails and the event
# falls through to the bounty handler. The caption check MUST live in the filter
# (a handler that ran and returned would consume the event and starve bounty).
def _wants_render(caption: Optional[str]) -> bool:
    return bool(caption) and any(kw in caption.lower() for kw in ("render", "рендер"))


@router.message(F.document, F.caption.func(_wants_render))
async def cmd_render_file(message: types.Message, osu_api_client=None, tenant_chat_id=None):
    doc = message.document
    if not doc or not (doc.file_name or "").lower().endswith(".osr"):
        return

    tg_id = message.from_user.id

    remaining = _check_cooldown(tg_id)
    if remaining:
        await message.answer(f"Подождите <b>{remaining} сек.</b> перед следующим рендером.", parse_mode="HTML")
        return

    # In remote mode the bot has no danser binary — the worker checks it.
    if not RENDER_WORKER_URL:
        try:
            danser_renderer._check_danser()
        except danser_renderer.DanserNotFoundError as e:
            await message.answer(str(e), parse_mode="HTML")
            return

    wait_msg = await message.answer("Загрузка реплея...", parse_mode="HTML")

    tmp_dir = tempfile.mkdtemp(prefix="render_osr_")
    video_path = None

    try:
        osr_path = os.path.join(tmp_dir, doc.file_name or "replay.osr")
        await message.bot.download(doc, destination=osr_path)

        # The .osr only names its beatmap by md5 — resolve it to a beatmapset and
        # fetch the .osz so danser can import the map (danser unpacks osz from its
        # Songs dir on the next run).
        await wait_msg.edit_text("Поиск карты по реплею...", parse_mode="HTML")
        try:
            with open(osr_path, "rb") as f:
                osr_bytes = f.read()
            md5 = Replay.from_string(osr_bytes).beatmap_hash
        except Exception as e:
            logger.info(f"render_file: osrparse failed for tg={tg_id}: {e}")
            await wait_msg.edit_text("Не удалось прочитать <code>.osr</code>.", parse_mode="HTML")
            return

        # Load settings + cache check on the .osr contents — re-send instantly if
        # this exact replay+settings was rendered before (no GPU, no map lookup).
        render_settings = None
        async with get_db_session() as session:
            user = await get_registered_user(session, tg_id, tenant_chat_id)
            if user:
                settings = await _get_or_create_settings(session, user.id)
                render_settings = _settings_to_dict(settings)

        osr_hash = hashlib.sha1(osr_bytes).hexdigest()
        cache_key = _cache_key(f"osr:{osr_hash}", render_settings)
        cached_file_id = await _cache_lookup(cache_key)
        if cached_file_id:
            try:
                await wait_msg.delete()
            except Exception:
                pass
            try:
                await message.answer_video(video=cached_file_id, supports_streaming=True)
                _cooldowns[tg_id] = time.time()
                return
            except Exception as e:
                logger.info(f"cached file_id failed, re-rendering: {e}")

        bm = await osu_api_client.lookup_beatmap_by_checksum(md5) if (osu_api_client and md5) else None
        beatmapset_id = (bm or {}).get("beatmapset_id")
        if not beatmapset_id:
            await wait_msg.edit_text(
                "Карта этого реплея не найдена на osu! (возможно, анранкнутая или удалённая).",
                parse_mode="HTML",
            )
            return

        # Download the .osz locally only in local mode — in remote mode the
        # worker fetches the map (the md5→beatmapset resolve above stays on the
        # bot because it needs the osu! API).
        if not RENDER_WORKER_URL:
            await wait_msg.edit_text("Загрузка карты...", parse_mode="HTML")
            if not await danser_renderer.download_beatmap(beatmapset_id):
                await wait_msg.edit_text("Не удалось скачать карту. Попробуйте позже.")
                return

        if RENDER_WORKER_URL:
            await wait_msg.edit_text("Рендеринг на удалённом сервере...", parse_mode="HTML")
        else:
            await wait_msg.edit_text("Рендеринг видео...", parse_mode="HTML")

        async def on_progress(text: str):
            try:
                await wait_msg.edit_text(text, parse_mode="HTML")
            except Exception:
                pass

        async def on_queue(position: int):
            try:
                await wait_msg.edit_text(
                    f"В очереди на рендер: <b>#{position}</b>. Ожидайте...",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        out_name = f"render_file_{tg_id}_{int(time.time())}"

        try:
            video_path, w, h, dur = await _do_render(
                osr_path, beatmapset_id, render_settings, out_name, on_progress, on_queue,
            )
        except danser_renderer.RenderQueueFullError:
            await wait_msg.edit_text("Слишком много рендеров в очереди. Попробуйте позже.")
            return
        except render_client.RenderWorkerUnreachable:
            await wait_msg.edit_text("Сервер рендеринга недоступен. Попробуйте позже.")
            return
        except danser_renderer.DanserError as e:
            error_text = str(e)
            if "beatmap" in error_text.lower() or "map" in error_text.lower():
                await wait_msg.edit_text(
                    "Ошибка: карта не найдена в базе danser.\n"
                    "Сначала используйте <code>render [ник]</code> чтобы карта загрузилась автоматически.",
                    parse_mode="HTML",
                )
            else:
                await wait_msg.edit_text(f"Ошибка рендеринга: {escape_html(error_text)}", parse_mode="HTML")
            return

        _cooldowns[tg_id] = time.time()
        file_size = os.path.getsize(video_path)

        if file_size <= MAX_VIDEO_BYTES:
            await wait_msg.edit_text("Отправка видео...", parse_mode="HTML")
            try:
                video_file = FSInputFile(video_path, filename="render.mp4")
                await wait_msg.delete()
                sent = await message.answer_video(
                    video=video_file, width=w, height=h, duration=dur,
                    supports_streaming=True,
                )
                if sent and sent.video:
                    await _cache_store(cache_key, sent.video.file_id)
            except Exception as e:
                logger.error(f"Failed to send video: {e}")
                await message.answer("Не удалось отправить видео в Telegram.")
        else:
            await wait_msg.edit_text(
                f"Видео слишком большое для Telegram ({file_size // (1024*1024)} МБ).",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Render file error: {e}")
        try:
            await wait_msg.edit_text("Произошла ошибка при рендере реплея.", parse_mode="HTML")
        except Exception:
            pass

    finally:
        try:
            if os.path.isdir(tmp_dir):
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        if video_path and os.path.isfile(video_path):
            try:
                os.remove(video_path)
            except Exception:
                pass


__all__ = ["router"]
