"""Render from an uploaded .osr file.

Any .osr upload prompts a one-tap confirm before rendering (a bare drop-in-chat
would otherwise wake the billed GPU on every accidental/spammed upload). This
path reuses the low-level pieces from pipeline (cooldown, _do_render,
MAX_VIDEO_BYTES) but runs its own flow — it resolves the beatmap from the
replay's md5 and downloads the file straight off the message rather than via a
score id.
"""

import os
import time
import hashlib
import tempfile

from aiogram import Router, F, types
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from osrparse import Replay
from osrparse.utils import GameMode

from config.settings import RENDER_WORKER_URL
from db.database import get_db_session
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language
from utils.osu.resolve_user import get_registered_user
from utils.osu import danser_renderer
from utils.osu import render_client
from bot.handlers.profile.render.user_settings import _get_or_create_settings, _settings_to_dict
from bot.handlers.profile.render.cache import _cache_key, _cache_lookup, _cache_store
from bot.handlers.profile.render.library import _render_label, store_user_render
from bot.handlers.profile.render.pipeline import (
    _check_cooldown, _cooldowns, _do_render, MAX_VIDEO_BYTES,
)

logger = get_logger("handlers.render")
router = Router(name="render_osr")


def _is_osr(doc) -> bool:
    return bool(doc) and (getattr(doc, "file_name", "") or "").lower().endswith(".osr")


def _confirm_render_kb(owner_tg_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("render.kb.confirm", lang), callback_data=f"rdrf:go:{owner_tg_id}"),
        InlineKeyboardButton(text=t("render.kb.cancel", lang), callback_data=f"rdrf:no:{owner_tg_id}"),
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
    lang = (await get_language(tg_id)).lower()
    remaining = _check_cooldown(tg_id)
    if remaining:
        await message.reply(t("render.wait_before_next", lang, sec=remaining), parse_mode="HTML")
        return
    await message.reply(t("render.confirm_prompt", lang), reply_markup=_confirm_render_kb(tg_id, lang))


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
    lang = (await get_language(callback.from_user.id)).lower()
    if callback.from_user.id != owner_tg_id:
        await callback.answer(t("render.not_your_replay", lang), show_alert=True)
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
        await callback.answer(t("render.file_gone", lang), show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _render_uploaded_osr(src, doc, osu_api_client=osu_api_client, tenant_chat_id=tenant_chat_id, lang=lang)


async def _render_uploaded_osr(message: types.Message, doc, osu_api_client=None, tenant_chat_id=None,
                               lang: str = "en"):
    tg_id = message.from_user.id

    remaining = _check_cooldown(tg_id)
    if remaining:
        await message.answer(t("render.wait_before_next", lang, sec=remaining), parse_mode="HTML")
        return

    # In remote mode the bot has no danser binary — the worker checks it.
    if not RENDER_WORKER_URL:
        try:
            danser_renderer._check_danser()
        except danser_renderer.DanserNotFoundError as e:
            await message.answer(str(e), parse_mode="HTML")
            return

    wait_msg = await message.answer(t("render.loading_replay", lang), parse_mode="HTML")

    tmp_dir = tempfile.mkdtemp(prefix="render_osr_")
    video_path = None

    try:
        osr_path = os.path.join(tmp_dir, doc.file_name or "replay.osr")
        await message.bot.download(doc, destination=osr_path)

        # The .osr only names its beatmap by md5 — resolve it to a beatmapset and
        # fetch the .osz so danser can import the map (danser unpacks osz from its
        # Songs dir on the next run).
        await wait_msg.edit_text(t("render.searching_map_by_replay", lang), parse_mode="HTML")
        replay_username = None
        try:
            with open(osr_path, "rb") as f:
                osr_bytes = f.read()
            _replay = Replay.from_string(osr_bytes)
            md5 = _replay.beatmap_hash
            replay_username = getattr(_replay, "username", None)
        except Exception as e:
            logger.info(f"render_file: osrparse failed for tg={tg_id}: {e}")
            await wait_msg.edit_text(t("render.osr_read_failed", lang), parse_mode="HTML")
            return

        # danser-go only knows how to render osu!standard (see its own repo
        # description) — a taiko/catch/mania replay would otherwise sail through
        # this whole pipeline and only fail once danser itself chokes on it,
        # surfacing as an opaque "danser exited with code 1".
        if _replay.mode != GameMode.STD:
            await wait_msg.edit_text(t("render.std_only", lang), parse_mode="HTML")
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

        osr_hash = hashlib.sha1(osr_bytes, usedforsecurity=False).hexdigest()
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
            await wait_msg.edit_text(t("render.map_not_found", lang), parse_mode="HTML")
            return

        # Download the .osz locally in local mode; in remote mode the bot
        # fetches it too (not the worker — its outbound internet is
        # bandwidth-limited and stalls on files this size, see
        # fetch_beatmap_osz's note) and hands the bytes over with the render
        # request. The md5→beatmapset resolve above stays on the bot either
        # way because it needs the osu! API.
        beatmap_osz_bytes = None
        if not RENDER_WORKER_URL:
            await wait_msg.edit_text(t("render.loading_map", lang), parse_mode="HTML")
            if not await danser_renderer.download_beatmap(beatmapset_id):
                await wait_msg.edit_text(t("render.map_download_failed", lang))
                return
        else:
            await wait_msg.edit_text(t("render.loading_map", lang), parse_mode="HTML")
            beatmap_osz_bytes = await danser_renderer.fetch_beatmap_osz(beatmapset_id)
            if not beatmap_osz_bytes:
                await wait_msg.edit_text(t("render.map_download_failed", lang))
                return

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

        out_name = f"render_file_{tg_id}_{int(time.time())}"

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
            error_text = str(e)
            if "beatmap" in error_text.lower() or "map" in error_text.lower():
                await wait_msg.edit_text(t("render.danser_map_missing", lang), parse_mode="HTML")
            else:
                await wait_msg.edit_text(t("render.render_error", lang, error=escape_html(error_text)), parse_mode="HTML")
            return

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
                    label = _render_label(meta) or t("render.osr_label", lang)
                    await store_user_render(
                        user_id, f"osr:{osr_hash}", sent.video.file_id, label, meta,
                    )
            except Exception as e:
                logger.error(f"Failed to send video: {e!r}", exc_info=True)
                await message.answer(t("render.send_failed", lang))
        else:
            await wait_msg.edit_text(
                t("render.video_too_large", lang, mb=file_size // (1024 * 1024)),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Render file error: {e!r}", exc_info=True)
        try:
            await wait_msg.edit_text(t("render.generic_error", lang), parse_mode="HTML")
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
