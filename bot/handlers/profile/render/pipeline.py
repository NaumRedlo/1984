"""Core render pipeline for a known score: cooldown/in-flight gating, the
local-or-remote render call, hybrid replay-token resolution, and the shared
"load settings → cache → download → render → send → store" flow.

The .osr-upload path (osr_handlers) reuses the low-level pieces here (_do_render,
the cooldown state, MAX_VIDEO_BYTES) but runs its own variant of the flow.
"""

import os
import time
import tempfile
from typing import Optional, Dict

from aiogram import types
from aiogram.types import FSInputFile

from config.settings import (
    RENDER_MAX_VIDEO_MB,
    RENDER_WORKER_URL,
    RENDER_SERVICE_OAUTH_TG_ID,
)
from db.database import get_db_session
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.osu.api_client import OsuApiClient
from utils.osu.resolve_user import get_registered_user
from utils.osu import danser_renderer
from utils.osu import render_client
from bot.handlers.profile.render.user_settings import _get_or_create_settings, _settings_to_dict
from bot.handlers.profile.render.cache import _cache_key, _cache_lookup, _cache_store
from bot.handlers.profile.render.library import _render_label, store_user_render

logger = get_logger("handlers.render")

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


async def _do_render(osr_path, beatmapset_id, render_settings, out_name, on_progress, on_queue,
                     beatmap_osz=None, lang: str = "en"):
    """Render a replay, locally or on the remote worker depending on
    RENDER_WORKER_URL. Returns (video_path, width, height, duration); video_path
    is a temp file the caller must delete. Raises the same danser_renderer
    exceptions in both modes (plus RenderWorkerUnreachable in remote mode).

    beatmap_osz: pre-fetched .osz bytes for remote mode — see
    danser_renderer.fetch_beatmap_osz's docstring for why the caller fetches
    this itself now instead of leaving it to the worker."""
    if RENDER_WORKER_URL:
        if on_progress:
            try:
                await on_progress(t("render.gpu_rendering", lang))
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
    lang: str = "en",
) -> None:
    """Shared pipeline once a score is known: load settings, check the cache,
    download the replay (hybrid token), render and send. `wait_msg` is a status
    message this owns (edited for progress, deleted before the video). `message`
    is only used to post the result, so it works for both a command message and a
    callback's card message. length_seconds (map playback length) lets the GPU
    render target a single-pass bitrate so it usually skips the fit re-encode.
    `meta` (map/score snapshot) is saved to the user's render library on send.
    `lang` is the requester's language for all status/error text."""
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
            await wait_msg.edit_text(t("render.loading_map", lang), parse_mode="HTML")
            map_ok = await danser_renderer.download_beatmap(beatmapset_id)
            if not map_ok:
                await wait_msg.edit_text(t("render.map_download_failed", lang))
                return
    elif beatmapset_id:
        # Fetch the .osz on the bot's own connection and hand the bytes to the
        # worker directly — the worker's own outbound internet is bandwidth-
        # limited and stalls on files this size (see fetch_beatmap_osz's note).
        await wait_msg.edit_text(t("render.loading_map", lang), parse_mode="HTML")
        beatmap_osz_bytes = await danser_renderer.fetch_beatmap_osz(beatmapset_id)
        if not beatmap_osz_bytes:
            await wait_msg.edit_text(t("render.map_download_failed", lang))
            return

    # Download replay (requester's token → service token → app token)
    await wait_msg.edit_text(t("render.loading_replay", lang), parse_mode="HTML")
    replay_token = await _resolve_replay_token(tg_id)

    tmp_dir = tempfile.mkdtemp(prefix="render_")
    video_path = None

    try:
        osr_path = await danser_renderer.download_replay_file(
            osu_api_client, score_id, tmp_dir, oauth_token=replay_token,
        )
        if not osr_path:
            await wait_msg.edit_text(t("render.replay_unavailable", lang), parse_mode="HTML")
            return

        # Render
        if RENDER_WORKER_URL:
            await wait_msg.edit_text(t("render.rendering_remote", lang), parse_mode="HTML")
        else:
            await wait_msg.edit_text(t("render.rendering_local", lang), parse_mode="HTML")

        async def on_progress(text: str):
            try:
                await wait_msg.edit_text(text, parse_mode="HTML")
            except Exception:
                pass

        async def on_queue(position: int):
            try:
                await wait_msg.edit_text(t("render.queue_position", lang, position=position), parse_mode="HTML")
            except Exception:
                pass

        out_name = f"render_{score_id}_{int(time.time())}"

        try:
            video_path, w, h, dur = await _do_render(
                osr_path, beatmapset_id, render_settings, out_name, on_progress, on_queue,
                beatmap_osz=beatmap_osz_bytes, lang=lang,
            )
        except danser_renderer.RenderQueueFullError:
            await wait_msg.edit_text(t("render.queue_full", lang))
            return
        except render_client.RenderWorkerUnreachable:
            await wait_msg.edit_text(t("render.worker_unreachable", lang))
            return
        except danser_renderer.DanserError as e:
            await wait_msg.edit_text(t("render.render_error", lang, error=escape_html(str(e))), parse_mode="HTML")
            return

        # Send video
        _cooldowns[tg_id] = time.time()
        file_size = os.path.getsize(video_path)

        if file_size <= MAX_VIDEO_BYTES:
            await wait_msg.edit_text(t("render.sending_video", lang), parse_mode="HTML")
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
                await message.answer(t("render.send_failed", lang))
        else:
            await wait_msg.edit_text(
                t("render.video_too_large", lang, mb=file_size // (1024 * 1024)),
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


# ── render gating (shared by the rs/recent-card button + library re-render) ──
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
                             length_seconds, meta, tg_id, tenant_chat_id, osu_api_client,
                             lang: str = "en"):
    """Post a status message and run the render under the in-flight guard. Callers
    must have passed render_gate() first."""
    wait_msg = await message.answer(
        t("render.preparing", lang, name=escape_html(display_name)), parse_mode="HTML")
    _RENDER_INFLIGHT.add(tg_id)
    try:
        await _render_and_send(
            message, wait_msg,
            score_id=score_id, beatmapset_id=beatmapset_id, display_name=display_name,
            tg_id=tg_id, tenant_chat_id=tenant_chat_id, osu_api_client=osu_api_client,
            length_seconds=length_seconds, meta=meta, lang=lang,
        )
    finally:
        _RENDER_INFLIGHT.discard(tg_id)
