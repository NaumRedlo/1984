"""Install a custom skin (.osk) — open to any registered player.

Two entry points: send an .osk file (≤ the cloud Bot API's 20 MB getFile limit),
or `skin <url>` for bigger skins the bot fetches over HTTP (SSRF-guarded). Each
install wakes the GPU worker, so it has its own (longer) per-user cooldown.
"""

import os
import time
import socket
import ipaddress
import tempfile
from urllib.parse import urlparse

import aiohttp
from aiogram import Router, F, types

from config.settings import RENDER_WORKER_URL
from db.database import get_db_session
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language
from utils.osu.resolve_user import get_registered_identity_user
from utils.osu import danser_renderer
from utils.osu import render_client
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.profile.render.skins import _add_render_skin

logger = get_logger("handlers.render")
router = Router(name="render_skin")

# Skin uploads are open to any registered player but each wakes the GPU worker, so
# they get their own (longer) per-user cooldown to keep costs down.
_skin_cooldowns: dict = {}
SKIN_COOLDOWN_SECONDS = 120

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


async def _download_osk_from_url(url: str, lang: str = "en"):
    """Fetch a .osk over HTTP, bypassing Telegram's file limit. Returns
    (bytes, None) or (None, error). SSRF-guarded (public host, no redirects),
    size-capped, and validated as a zip. `error` is localised to `lang`."""
    try:
        p = urlparse(url)
    except Exception:
        return None, t("skin.bad_link", lang)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None, t("skin.bad_scheme", lang)
    if not _is_public_host(p.hostname):
        return None, t("skin.bad_host", lang)
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
    try:
        timeout = aiohttp.ClientTimeout(total=90, sock_connect=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
            # allow_redirects=False so a 3xx can't bounce us to an internal host.
            async with sess.get(url, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    return None, t("skin.redirect", lang)
                if resp.status != 200:
                    return None, t("skin.download_http_error", lang, status=resp.status)
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(256 * 1024):
                    buf += chunk
                    if len(buf) > _SKIN_URL_MAX_BYTES:
                        return None, t("skin.too_large", lang, mb=_SKIN_URL_MAX_BYTES // (1024 * 1024))
                data = bytes(buf)
    except Exception as e:
        logger.info(f"skin url download failed: {e}")
        return None, t("skin.download_failed", lang)
    if len(data) < 100 or data[:2] != b"PK":
        return None, t("skin.not_osk", lang)
    return data, None


async def _skin_precheck(message: types.Message, tg_id: int, lang: str = "en") -> bool:
    """Shared gate for both skin entry points: registered + remote mode + cooldown.
    Answers the user on failure. Returns True when it's OK to proceed."""
    async with get_db_session() as session:
        if not await get_registered_identity_user(session, tg_id):
            return False  # silently ignore non-registered
    if not RENDER_WORKER_URL:
        await message.answer(t("skin.remote_only", lang))
        return False
    last = _skin_cooldowns.get(tg_id)
    if last and time.time() - last < SKIN_COOLDOWN_SECONDS:
        rem = int(SKIN_COOLDOWN_SECONDS - (time.time() - last))
        await message.answer(t("skin.cooldown", lang, sec=rem), parse_mode="HTML")
        return False
    return True


async def _install_skin_bytes(message: types.Message, tg_id: int, osk_bytes: bytes, name: str,
                              lang: str = "en") -> None:
    """Install the .osk bytes on the remote worker, register the name, set cooldown."""
    wait_msg = await message.answer(t("skin.uploading", lang), parse_mode="HTML")

    try:
        installed = await render_client.install_skin_remote(osk_bytes, name)
    except render_client.RenderWorkerUnreachable:
        await wait_msg.edit_text(t("render.worker_unreachable", lang))
        return
    except danser_renderer.DanserError as e:
        await wait_msg.edit_text(t("skin.install_error", lang, error=escape_html(str(e))), parse_mode="HTML")
        return
    except Exception as e:
        logger.error(f"Skin install error: {e}")
        await wait_msg.edit_text(t("skin.install_failed", lang))
        return

    await _add_render_skin(installed, owner_tg_id=tg_id)
    _skin_cooldowns[tg_id] = time.time()
    await wait_msg.edit_text(
        t("skin.installed", lang, name=escape_html(installed)),
        parse_mode="HTML",
    )


@router.message(F.document.func(_is_osk))
async def cmd_install_skin(message: types.Message, tenant_chat_id=None):
    tg_id = message.from_user.id
    lang = (await get_language(tg_id)).lower()
    if not await _skin_precheck(message, tg_id, lang):
        return

    doc = message.document
    # The bot can't download files past the cloud Bot API limit — tell the player
    # to send a link instead of failing with a generic error.
    if (doc.file_size or 0) > _TG_DOWNLOAD_LIMIT:
        await message.answer(
            t("skin.tg_too_large", lang, mb=_TG_DOWNLOAD_LIMIT // (1024 * 1024)),
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
            await message.answer(t("skin.tg_download_failed", lang), parse_mode="HTML")
            return
        with open(osk_path, "rb") as f:
            osk_bytes = f.read()
        await _install_skin_bytes(message, tg_id, osk_bytes, name, lang)
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.message(TextTriggerFilter("skin"))
async def cmd_install_skin_url(message: types.Message, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id
    lang = (await get_language(tg_id)).lower()
    args = (trigger_args.args or "").strip() if trigger_args else ""
    if not args:
        await message.answer(t("skin.usage", lang), parse_mode="HTML")
        return

    if not await _skin_precheck(message, tg_id, lang):
        return

    parts = args.split(maxsplit=1)
    url = parts[0]
    # Name = explicit 2nd arg, else the URL's filename (without .osk), else "skin".
    if len(parts) > 1 and parts[1].strip():
        name = parts[1].strip()
    else:
        base = os.path.basename(urlparse(url).path) or "skin"
        name = base[:-4] if base.lower().endswith(".osk") else base

    wait = await message.answer(t("skin.downloading", lang), parse_mode="HTML")
    osk_bytes, err = await _download_osk_from_url(url, lang)
    if err:
        await wait.edit_text(err)
        return
    try:
        await wait.delete()
    except Exception:
        pass
    await _install_skin_bytes(message, tg_id, osk_bytes, name, lang)
