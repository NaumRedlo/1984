import os
import time
import json
import socket
import hashlib
import ipaddress
import tempfile
from typing import Optional, Dict
from urllib.parse import urlparse

import aiohttp
from aiogram import Router, F, types
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from osrparse import Replay
from osrparse.utils import GameMode
from sqlalchemy import select, delete, update

from config.settings import (
    RENDER_MAX_VIDEO_MB,
    RENDER_WORKER_URL,
    RENDER_SERVICE_OAUTH_TG_ID,
)
from db.database import get_db_session
from db.models.render_settings import UserRenderSettings
from db.models.render_cache import RenderCache
from db.models.user_render import UserRender
from db.models.bot_settings import BotSettings
from utils.logger import get_logger
from utils.timeutils import utcnow
from utils.formatting.text import escape_html
from utils.osu.api_client import OsuApiClient
from utils.osu.resolve_user import get_registered_user, get_registered_identity_user
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

# Skin uploads are open to any registered player but each wakes the GPU worker, so
# they get their own (longer) per-user cooldown to keep costs down.
_skin_cooldowns: Dict[int, float] = {}
SKIN_COOLDOWN_SECONDS = 120

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
        "show_score": settings.show_score,
        "show_hp_bar": settings.show_hp_bar,
        "show_seizure_warning": settings.show_seizure_warning,
        "use_skin_hitsounds": settings.use_skin_hitsounds,
        "music_volume": settings.music_volume,
        "hitsound_volume": settings.hitsound_volume,
        "cinema_mode": settings.cinema_mode,
        "bg_dim": settings.bg_dim,
    }


async def _do_render(osr_path, beatmapset_id, render_settings, out_name, on_progress, on_queue, beatmap_osz=None):
    """Render a replay, locally or on the remote worker depending on
    RENDER_WORKER_URL. Returns (video_path, width, height, duration); video_path
    is a temp file the caller must delete. Raises the same danser_renderer
    exceptions in both modes (plus RenderWorkerUnreachable in remote mode).

    beatmap_osz: pre-fetched .osz bytes for remote mode — see
    danser_renderer.fetch_beatmap_osz's docstring for why the caller fetches
    this itself now instead of leaving it to the worker."""
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
            return await render_client.render_remote(osr_bytes, beatmapset_id, render_settings, beatmap_osz)

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
    "skin", "resolution", "bg_dim", "cursor_size",
    "show_pp_counter", "show_scoreboard", "show_key_overlay",
    "show_hit_error_meter", "show_mods", "show_result_screen",
    "show_strain_graph", "show_hit_counter", "show_score", "show_hp_bar",
    "show_seizure_warning", "use_skin_hitsounds", "music_volume", "hitsound_volume",
    "cinema_mode",
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


# ── per-user render library ("Мои рендеры" in /settings) ──
# Each finished render stores its Telegram file_id + a metadata snapshot, deduped
# per (user, score). Re-sending from here costs nothing (file_id), so the only
# bound is _MAX_USER_RENDERS — oldest are pruned.
_MAX_USER_RENDERS = 50


def _meta_from_ctx(ctx: dict) -> dict:
    """Snapshot the score details from a recent-card context for the library.
    beatmapset_id + length are kept so a stale entry can be re-rendered."""
    return {
        "artist": ctx.get("artist"),
        "title": ctx.get("title"),
        "version": ctx.get("version"),
        "mods": ctx.get("mods"),
        "rank": ctx.get("rank_grade"),
        "pp": ctx.get("pp"),
        "acc": ctx.get("accuracy"),
        "stars": ctx.get("star_rating"),
        "combo": ctx.get("combo"),
        "misses": ctx.get("misses"),
        "player": ctx.get("username"),
        "beatmapset_id": ctx.get("beatmapset_id"),
        "length": ctx.get("total_length"),
    }


def _render_label(meta: dict) -> str:
    """Short one-line label for the library list ('Artist - Title')."""
    if not meta:
        return ""
    artist = (meta.get("artist") or "").strip()
    title = (meta.get("title") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    return title or (meta.get("label") or "")


async def store_user_render(user_id, ref: str, file_id: str, label: str, meta: dict) -> None:
    if not user_id or not file_id:
        return
    async with get_db_session() as session:
        existing = (await session.execute(
            select(UserRender).where(UserRender.user_id == user_id, UserRender.ref == ref)
        )).scalar_one_or_none()
        payload = json.dumps(meta, ensure_ascii=False)
        if existing:
            existing.file_id = file_id
            existing.label = (label or "")[:255]
            existing.meta = payload
            existing.created_at = utcnow()
        else:
            session.add(UserRender(
                user_id=user_id, ref=ref, file_id=file_id,
                label=(label or "")[:255], meta=payload,
            ))
        await session.commit()
        # Prune anything past the newest _MAX_USER_RENDERS.
        ids = (await session.execute(
            select(UserRender.id).where(UserRender.user_id == user_id)
            .order_by(UserRender.created_at.desc())
        )).scalars().all()
        if len(ids) > _MAX_USER_RENDERS:
            await session.execute(delete(UserRender).where(UserRender.id.in_(ids[_MAX_USER_RENDERS:])))
            await session.commit()


async def get_user_renders(user_id) -> list:
    async with get_db_session() as session:
        return list((await session.execute(
            select(UserRender).where(UserRender.user_id == user_id)
            .order_by(UserRender.created_at.desc())
        )).scalars().all())


async def get_user_render(user_id, render_id):
    async with get_db_session() as session:
        return (await session.execute(
            select(UserRender).where(UserRender.id == render_id, UserRender.user_id == user_id)
        )).scalar_one_or_none()


async def delete_user_render(user_id, render_id) -> None:
    async with get_db_session() as session:
        await session.execute(delete(UserRender).where(
            UserRender.id == render_id, UserRender.user_id == user_id))
        await session.commit()


# ── custom skins ──
# Installed skin files live on the worker (danser's Skins dir); the bot keeps just
# the list of names here so the /settings picker works even when the on-demand GPU
# is asleep. Each entry also records the uploader's tg_id ("owner") so only they
# can rename/delete it later — entries from before ownership tracking existed (or
# a malformed row) have owner=None and are treated as un-manageable, select-only.
_SKINS_KEY = "render_skins"


async def get_render_skins() -> list:
    """[{'name': str, 'owner': Optional[int]}, ...] uploaded skins (not 'default')."""
    async with get_db_session() as session:
        row = (await session.execute(
            select(BotSettings).where(BotSettings.key == _SKINS_KEY)
        )).scalar_one_or_none()
        if not (row and row.value):
            return []
        try:
            raw = json.loads(row.value)
        except Exception:
            return []
        out = []
        for entry in raw:
            if isinstance(entry, str):
                out.append({"name": entry, "owner": None})  # legacy, uploader unknown
            elif isinstance(entry, dict) and entry.get("name"):
                out.append({"name": entry["name"], "owner": entry.get("owner")})
        return out


async def get_my_render_skins(tg_id: int) -> list:
    """Skins uploaded by this tg_id — the only ones they may rename/delete."""
    return [e for e in await get_render_skins() if e.get("owner") == tg_id]


async def _save_render_skins(entries: list) -> None:
    async with get_db_session() as session:
        row = (await session.execute(
            select(BotSettings).where(BotSettings.key == _SKINS_KEY)
        )).scalar_one_or_none()
        value = json.dumps(entries)
        if row:
            row.value = value
        else:
            session.add(BotSettings(key=_SKINS_KEY, value=value))
        await session.commit()


async def _add_render_skin(name: str, owner_tg_id: Optional[int] = None) -> None:
    entries = await get_render_skins()
    for e in entries:
        if e["name"] == name:
            if e.get("owner") is None and owner_tg_id is not None:
                e["owner"] = owner_tg_id  # claim a previously-unowned re-upload
            break
    else:
        entries.append({"name": name, "owner": owner_tg_id})
    await _save_render_skins(entries)


async def _remove_render_skin(name: str) -> None:
    entries = [e for e in await get_render_skins() if e["name"] != name]
    await _save_render_skins(entries)


async def _rename_render_skin_entry(name: str, new_name: str) -> None:
    entries = await get_render_skins()
    for e in entries:
        if e["name"] == name:
            e["name"] = new_name
    await _save_render_skins(entries)


async def _reassign_users_off_skin(old_name: str, new_name: str = "default") -> None:
    """Point any player's UserRenderSettings.skin away from a skin that just got
    renamed or deleted, so their next render doesn't reference a missing/stale
    folder name on the worker."""
    async with get_db_session() as session:
        await session.execute(
            update(UserRenderSettings)
            .where(UserRenderSettings.skin == old_name)
            .values(skin=new_name)
        )
        await session.commit()


async def do_delete_skin(status_message: types.Message, name: str) -> None:
    """Wake the worker, delete the skin folder, then clean up bot-side records
    (drop from the list, fall any current users of it back to 'default').
    Raises render_client.RenderWorkerUnreachable / danser_renderer.DanserError."""
    async def on_wake(text: str):
        try:
            await status_message.edit_text(text, parse_mode="HTML")
        except Exception:
            pass

    async with gpu_power.session(on_wake=on_wake):
        await render_client.delete_skin_remote(name)
    await _remove_render_skin(name)
    await _reassign_users_off_skin(name, "default")


async def do_rename_skin(status_message: types.Message, name: str, new_name: str) -> str:
    """Wake the worker, rename the skin folder, then update bot-side records
    (the list entry, and anyone currently using it). Returns the sanitized name
    actually used. Raises render_client.RenderWorkerUnreachable / DanserError."""
    async def on_wake(text: str):
        try:
            await status_message.edit_text(text, parse_mode="HTML")
        except Exception:
            pass

    async with gpu_power.session(on_wake=on_wake):
        final_name = await render_client.rename_skin_remote(name, new_name)
    await _rename_render_skin_entry(name, final_name)
    await _reassign_users_off_skin(name, final_name)
    return final_name


# ── replay download token ──

async def _resolve_replay_token(requester_tg_id: int) -> Optional[str]:
    """Pick the best token for downloading a replay, most-specific first:
    the requester's own OAuth token, then the shared service account, then None
    (download_replay falls back to the guest app token, which usually 401/403s).
    A user token can download *any* downloadable replay, not just its owner's, so
    the service token lets unlinked players render too — maximising coverage."""
    token = await OsuApiClient.try_get_oauth_token(requester_tg_id)
    if token:
        return token
    if RENDER_SERVICE_OAUTH_TG_ID:
        token = await OsuApiClient.try_get_oauth_token(RENDER_SERVICE_OAUTH_TG_ID)
        if token:
            return token
    return None


async def _render_and_send(
    message: types.Message,
    wait_msg: types.Message,
    *,
    score_id: int,
    beatmapset_id,
    display_name: str,
    tg_id: int,
    tenant_chat_id,
    osu_api_client,
    length_seconds=None,
    meta=None,
) -> None:
    """Shared pipeline once a score is known: load settings, check the cache,
    download the replay (hybrid token), render and send. `wait_msg` is a status
    message this owns (edited for progress, deleted before the video). `message`
    is only used to post the result, so it works for both a command message and a
    callback's card message. length_seconds (map playback length) lets the GPU
    render target a single-pass bitrate so it usually skips the fit re-encode.
    `meta` (map/score snapshot) is saved to the user's render library on send."""
    # Load render settings up front — needed for the cache key.
    render_settings = None
    user_id = None
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        if user:
            user_id = user.id
            settings = await _get_or_create_settings(session, user.id)
            render_settings = _settings_to_dict(settings)
    # Length steers the encoder bitrate, not the output identity — keep it out of
    # the cache signature (same score -> same length anyway).
    if render_settings is not None and length_seconds:
        render_settings["length_seconds"] = length_seconds

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

    # Check danser availability (local mode only — in remote mode danser lives
    # on the worker, and the bot has no danser binary).
    beatmap_osz_bytes = None
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
    elif beatmapset_id:
        # Fetch the .osz on the bot's own connection and hand the bytes to the
        # worker directly — the worker's own outbound internet is bandwidth-
        # limited and stalls on files this size (see fetch_beatmap_osz's note).
        await wait_msg.edit_text("Загрузка карты...", parse_mode="HTML")
        beatmap_osz_bytes = await danser_renderer.fetch_beatmap_osz(beatmapset_id)
        if not beatmap_osz_bytes:
            await wait_msg.edit_text("Не удалось скачать карту. Попробуйте позже.")
            return

    # Download replay (requester's token → service token → app token)
    await wait_msg.edit_text("Загрузка реплея...", parse_mode="HTML")
    replay_token = await _resolve_replay_token(tg_id)

    tmp_dir = tempfile.mkdtemp(prefix="render_")
    video_path = None

    try:
        osr_path = await danser_renderer.download_replay_file(
            osu_api_client, score_id, tmp_dir, oauth_token=replay_token,
        )
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
                beatmap_osz=beatmap_osz_bytes,
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
                    m = meta or {}
                    label = _render_label(m) or display_name or "Render"
                    await store_user_render(
                        user_id, f"score:{score_id}", sent.video.file_id, label, m,
                    )
            except Exception as e:
                logger.error(f"Failed to send video: {e!r}", exc_info=True)
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


# ── render last score via inline button (on rs/recent cards) ──
# Rendering is button-only (the rdr text command was removed): the 🎬 button on a
# recent card carries the score in its callback. Abuse guards: a per-user cooldown
# (COOLDOWN_SECONDS) AND an in-flight set so rapid taps can't queue duplicate
# renders or hammer the cache before the cooldown timestamp is even written.

_RENDER_INFLIGHT: set = set()


def render_gate(tg_id) -> Optional[str]:
    """Pre-flight for a render: returns 'cooldown:<sec>' or 'busy' if the user
    can't start one right now, else None. Shared by the button + library re-render."""
    remaining = _check_cooldown(tg_id)
    if remaining:
        return f"cooldown:{remaining}"
    if tg_id in _RENDER_INFLIGHT:
        return "busy"
    return None


async def run_guarded_render(message, *, score_id, beatmapset_id, display_name,
                             length_seconds, meta, tg_id, tenant_chat_id, osu_api_client):
    """Post a status message and run the render under the in-flight guard. Callers
    must have passed render_gate() first."""
    wait_msg = await message.answer(
        f"Подготовка рендера <b>{escape_html(display_name)}</b>...", parse_mode="HTML")
    _RENDER_INFLIGHT.add(tg_id)
    try:
        await _render_and_send(
            message, wait_msg,
            score_id=score_id, beatmapset_id=beatmapset_id, display_name=display_name,
            tg_id=tg_id, tenant_chat_id=tenant_chat_id, osu_api_client=osu_api_client,
            length_seconds=length_seconds, meta=meta,
        )
    finally:
        _RENDER_INFLIGHT.discard(tg_id)


@router.callback_query(F.data.startswith("rndr:"))
async def cb_render_score(callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None):
    tg_id = callback.from_user.id

    gate = render_gate(tg_id)
    if gate == "busy":
        await callback.answer("Дождитесь завершения текущего рендера.", show_alert=True)
        return
    if gate and gate.startswith("cooldown:"):
        await callback.answer(f"Подождите {gate.split(':')[1]} сек.", show_alert=True)
        return

    try:
        score_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    # The card's stored context carries the beatmapset + player name + length, plus
    # the score details we snapshot into the render library.
    ctx = get_message_context(callback.message.chat.id, callback.message.message_id) or {}
    beatmapset_id = ctx.get("beatmapset_id")
    display_name = ctx.get("username", "")
    length_seconds = ctx.get("total_length")
    meta = _meta_from_ctx(ctx)

    await callback.answer("Рендер запущен...")
    await run_guarded_render(
        callback.message, score_id=score_id, beatmapset_id=beatmapset_id,
        display_name=display_name, length_seconds=length_seconds, meta=meta,
        tg_id=tg_id, tenant_chat_id=tenant_chat_id, osu_api_client=osu_api_client,
    )


# ── render from .osr file ──

def _is_osr(doc) -> bool:
    return bool(doc) and (getattr(doc, "file_name", "") or "").lower().endswith(".osr")


def _confirm_render_kb(owner_tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎬 Рендерить", callback_data=f"rdrf:go:{owner_tg_id}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"rdrf:no:{owner_tg_id}"),
    ]])


# Any .osr upload prompts a one-tap confirm instead of rendering immediately —
# with no caption/trigger word required, a bare drop-in-chat would otherwise
# wake the (billed) GPU on every accidental or spammed upload. The prompt is
# sent as a REPLY to the upload so the confirm callback can read the document
# straight off `callback.message.reply_to_message` — no separate pending-
# render store needed. (Used to also gate against bounty's own bare-`.osr`
# handler — that feature was removed outright, so .osr is render's alone now.)
@router.message(F.document.func(_is_osr))
async def prompt_render_file(message: types.Message, osu_api_client=None, tenant_chat_id=None):
    tg_id = message.from_user.id
    remaining = _check_cooldown(tg_id)
    if remaining:
        await message.reply(f"Подождите <b>{remaining} сек.</b> перед следующим рендером.", parse_mode="HTML")
        return
    await message.reply("🎬 Отрендерить этот реплей?", reply_markup=_confirm_render_kb(tg_id))


@router.callback_query(F.data.startswith("rdrf:"))
async def cb_confirm_render_file(callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None):
    parts = callback.data.split(":", 2)  # rdrf:go|no:<owner_tg_id>
    if len(parts) != 3:
        await callback.answer()
        return
    action, owner_str = parts[1], parts[2]
    try:
        owner_tg_id = int(owner_str)
    except ValueError:
        await callback.answer()
        return
    if callback.from_user.id != owner_tg_id:
        await callback.answer("Не ваш реплей.", show_alert=True)
        return

    if action == "no":
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()
        return

    src = callback.message.reply_to_message
    doc = src.document if src else None
    if not doc or not _is_osr(doc):
        await callback.answer("Файл реплея больше недоступен, загрузите заново.", show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _render_uploaded_osr(src, doc, osu_api_client=osu_api_client, tenant_chat_id=tenant_chat_id)


async def _render_uploaded_osr(message: types.Message, doc, osu_api_client=None, tenant_chat_id=None):
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
        replay_username = None
        try:
            with open(osr_path, "rb") as f:
                osr_bytes = f.read()
            _replay = Replay.from_string(osr_bytes)
            md5 = _replay.beatmap_hash
            replay_username = getattr(_replay, "username", None)
        except Exception as e:
            logger.info(f"render_file: osrparse failed for tg={tg_id}: {e}")
            await wait_msg.edit_text("Не удалось прочитать <code>.osr</code>.", parse_mode="HTML")
            return

        # danser-go only knows how to render osu!standard (see its own repo
        # description) — a taiko/catch/mania replay would otherwise sail through
        # this whole pipeline and only fail once danser itself chokes on it,
        # surfacing as an opaque "danser exited with code 1".
        if _replay.mode != GameMode.STD:
            await wait_msg.edit_text(
                "Поддерживаются только реплеи <b>osu!standard</b>.", parse_mode="HTML",
            )
            return

        # Load settings + cache check on the .osr contents — re-send instantly if
        # this exact replay+settings was rendered before (no GPU, no map lookup).
        render_settings = None
        user_id = None
        async with get_db_session() as session:
            user = await get_registered_user(session, tg_id, tenant_chat_id)
            if user:
                user_id = user.id
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
        # Map length lets the GPU render target a single-pass bitrate (skips fit).
        if render_settings is not None and (bm or {}).get("total_length"):
            render_settings["length_seconds"] = bm["total_length"]
        # Snapshot for the render library (from the looked-up map + replay header).
        _bmset = (bm or {}).get("beatmapset") or {}
        meta = {
            "artist": _bmset.get("artist"),
            "title": _bmset.get("title"),
            "version": (bm or {}).get("version"),
            "stars": (bm or {}).get("difficulty_rating"),
            "player": replay_username,
            "beatmapset_id": beatmapset_id,
            "length": (bm or {}).get("total_length"),
        }
        if not beatmapset_id:
            await wait_msg.edit_text(
                "Карта этого реплея не найдена на osu! (возможно, анранкнутая или удалённая).",
                parse_mode="HTML",
            )
            return

        # Download the .osz locally in local mode; in remote mode the bot
        # fetches it too (not the worker — its outbound internet is
        # bandwidth-limited and stalls on files this size, see
        # fetch_beatmap_osz's note) and hands the bytes over with the render
        # request. The md5→beatmapset resolve above stays on the bot either
        # way because it needs the osu! API.
        beatmap_osz_bytes = None
        if not RENDER_WORKER_URL:
            await wait_msg.edit_text("Загрузка карты...", parse_mode="HTML")
            if not await danser_renderer.download_beatmap(beatmapset_id):
                await wait_msg.edit_text("Не удалось скачать карту. Попробуйте позже.")
                return
        else:
            await wait_msg.edit_text("Загрузка карты...", parse_mode="HTML")
            beatmap_osz_bytes = await danser_renderer.fetch_beatmap_osz(beatmapset_id)
            if not beatmap_osz_bytes:
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
                beatmap_osz=beatmap_osz_bytes,
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
                    "Сначала отрендерьте этот скор через <code>rs</code> → 🎬, чтобы карта загрузилась автоматически.",
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
                    label = _render_label(meta) or "Реплей (.osr)"
                    await store_user_render(
                        user_id, f"osr:{osr_hash}", sent.video.file_id, label, meta,
                    )
            except Exception as e:
                logger.error(f"Failed to send video: {e!r}", exc_info=True)
                await message.answer("Не удалось отправить видео в Telegram.")
        else:
            await wait_msg.edit_text(
                f"Видео слишком большое для Telegram ({file_size // (1024*1024)} МБ).",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Render file error: {e!r}", exc_info=True)
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


# ── install a custom skin (.osk) — any registered player ──
# Two entry points: send an .osk file (≤ the cloud Bot API's 20 MB getFile limit),
# or `skin <url>` for bigger skins the bot fetches over HTTP (no Telegram cap).

# Cloud Bot API caps a bot's getFile download at 20 MB — bigger .osk must come by URL.
_TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024
# Hard cap on a URL-fetched skin so a bad link can't exhaust disk/memory.
_SKIN_URL_MAX_BYTES = 150 * 1024 * 1024


def _is_osk(doc) -> bool:
    return bool(doc) and (getattr(doc, "file_name", "") or "").lower().endswith(".osk")


def _is_public_host(host: str) -> bool:
    """Reject SSRF targets: only allow hosts that resolve to public IPs."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


async def _download_osk_from_url(url: str):
    """Fetch a .osk over HTTP, bypassing Telegram's file limit. Returns
    (bytes, None) or (None, error). SSRF-guarded (public host, no redirects),
    size-capped, and validated as a zip."""
    try:
        p = urlparse(url)
    except Exception:
        return None, "Некорректная ссылка."
    if p.scheme not in ("http", "https") or not p.hostname:
        return None, "Ссылка должна начинаться с http:// или https://."
    if not _is_public_host(p.hostname):
        return None, "Недопустимый адрес."
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
    try:
        timeout = aiohttp.ClientTimeout(total=90, sock_connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
            # allow_redirects=False so a 3xx can't bounce us to an internal host.
            async with sess.get(url, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    return None, "Ссылка редиректит — дайте прямую ссылку на .osk."
                if resp.status != 200:
                    return None, f"Не удалось скачать (HTTP {resp.status})."
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(256 * 1024):
                    buf += chunk
                    if len(buf) > _SKIN_URL_MAX_BYTES:
                        return None, f"Файл слишком большой (> {_SKIN_URL_MAX_BYTES // (1024 * 1024)} МБ)."
                data = bytes(buf)
    except Exception as e:
        logger.info(f"skin url download failed: {e}")
        return None, "Не удалось скачать по ссылке."
    if len(data) < 100 or data[:2] != b"PK":
        return None, "Это не похоже на файл .osk (zip)."
    return data, None


async def _skin_precheck(message: types.Message, tg_id: int) -> bool:
    """Shared gate for both skin entry points: registered + remote mode + cooldown.
    Answers the user on failure. Returns True when it's OK to proceed."""
    async with get_db_session() as session:
        if not await get_registered_identity_user(session, tg_id):
            return False  # silently ignore non-registered
    if not RENDER_WORKER_URL:
        await message.answer("Загрузка скинов доступна только в режиме удалённого рендера.")
        return False
    last = _skin_cooldowns.get(tg_id)
    if last and time.time() - last < SKIN_COOLDOWN_SECONDS:
        rem = int(SKIN_COOLDOWN_SECONDS - (time.time() - last))
        await message.answer(f"Подождите <b>{rem} сек.</b> перед загрузкой следующего скина.", parse_mode="HTML")
        return False
    return True


async def _install_skin_bytes(message: types.Message, tg_id: int, osk_bytes: bytes, name: str) -> None:
    """Wake the GPU worker, install the .osk bytes, register the name, set cooldown."""
    wait_msg = await message.answer("Загрузка скина на сервер...", parse_mode="HTML")

    async def on_wake(text: str):
        try:
            await wait_msg.edit_text(text, parse_mode="HTML")
        except Exception:
            pass

    try:
        async with gpu_power.session(on_wake=on_wake):
            installed = await render_client.install_skin_remote(osk_bytes, name)
    except render_client.RenderWorkerUnreachable:
        await wait_msg.edit_text("Сервер рендеринга недоступен. Попробуйте позже.")
        return
    except danser_renderer.DanserError as e:
        await wait_msg.edit_text(f"Ошибка установки скина: {escape_html(str(e))}", parse_mode="HTML")
        return
    except Exception as e:
        logger.error(f"Skin install error: {e}")
        await wait_msg.edit_text("Ошибка при установке скина.")
        return

    await _add_render_skin(installed, owner_tg_id=tg_id)
    _skin_cooldowns[tg_id] = time.time()
    await wait_msg.edit_text(
        f"Скин установлен: <b>{escape_html(installed)}</b>\n"
        f"Выберите его в <code>sts</code> → 🎨 Видео.",
        parse_mode="HTML",
    )


@router.message(F.document.func(_is_osk))
async def cmd_install_skin(message: types.Message, tenant_chat_id=None):
    tg_id = message.from_user.id
    if not await _skin_precheck(message, tg_id):
        return

    doc = message.document
    # The bot can't download files past the cloud Bot API limit — tell the player
    # to send a link instead of failing with a generic error.
    if (doc.file_size or 0) > _TG_DOWNLOAD_LIMIT:
        await message.answer(
            f"Файл слишком большой для Telegram (> {_TG_DOWNLOAD_LIMIT // (1024 * 1024)} МБ).\n"
            f"Пришлите ссылку: <code>skin &lt;прямая ссылка на .osk&gt;</code>",
            parse_mode="HTML",
        )
        return

    caption = (message.caption or "").strip()
    name = caption or (doc.file_name or "skin")
    tmp_dir = tempfile.mkdtemp(prefix="skin_")
    try:
        osk_path = os.path.join(tmp_dir, doc.file_name or "skin.osk")
        try:
            await message.bot.download(doc, destination=osk_path)
        except Exception as e:
            logger.info(f"skin telegram download failed for tg={tg_id}: {e}")
            await message.answer(
                "Не удалось скачать файл из Telegram. Если он большой — пришлите "
                "ссылку: <code>skin &lt;ссылка на .osk&gt;</code>",
                parse_mode="HTML",
            )
            return
        with open(osk_path, "rb") as f:
            osk_bytes = f.read()
        await _install_skin_bytes(message, tg_id, osk_bytes, name)
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.message(TextTriggerFilter("skin"))
async def cmd_install_skin_url(message: types.Message, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    args = (trigger_args.args or "").strip() if trigger_args else ""
    if not args:
        await message.answer(
            "Использование: <code>skin &lt;прямая ссылка на .osk&gt; [название]</code>\n"
            "Для больших скинов (Telegram не принимает файлы > 20 МБ).",
            parse_mode="HTML",
        )
        return

    tg_id = message.from_user.id
    if not await _skin_precheck(message, tg_id):
        return

    parts = args.split(maxsplit=1)
    url = parts[0]
    # Name = explicit 2nd arg, else the URL's filename (without .osk), else "skin".
    if len(parts) > 1 and parts[1].strip():
        name = parts[1].strip()
    else:
        base = os.path.basename(urlparse(url).path) or "skin"
        name = base[:-4] if base.lower().endswith(".osk") else base

    wait = await message.answer("Скачиваю скин по ссылке...", parse_mode="HTML")
    osk_bytes, err = await _download_osk_from_url(url)
    if err:
        await wait.edit_text(err)
        return
    try:
        await wait.delete()
    except Exception:
        pass
    await _install_skin_bytes(message, tg_id, osk_bytes, name)


__all__ = ["router"]
