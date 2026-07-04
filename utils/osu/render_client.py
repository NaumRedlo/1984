"""Bot-side client for the remote render worker (services/render_worker).

When RENDER_WORKER_URL is set, the bot POSTs the .osr replay (plus the resolved
beatmapset_id and the user's render settings) to the worker and streams the
returned mp4 to a temp file. All osu! credentials stay on the bot; the worker
only runs danser.
"""

import asyncio
import json
import os
import tempfile
from typing import Optional, Tuple

import aiohttp

from config.settings import RENDER_WORKER_URL, RENDER_WORKER_SECRET, RENDER_WORKER_READ_TIMEOUT
from utils.logger import get_logger
from utils.osu.danser_renderer import DanserError, RenderQueueFullError

logger = get_logger("render_client")


class RenderWorkerUnreachable(DanserError):
    """Connection refused / DNS / timeout reaching the worker. Subclasses
    DanserError so existing `except DanserError` handlers still catch it; the
    render handlers add an explicit branch for a clearer offline message."""


async def render_remote(
    replay_bytes: bytes,
    beatmapset_id: Optional[int],
    settings: Optional[dict],
    beatmap_osz: Optional[bytes] = None,
) -> Tuple[str, Optional[int], Optional[int], Optional[int]]:
    """POST the replay to the worker and stream the mp4 to a temp file.

    beatmap_osz: the .osz bytes, if the caller already fetched them (see
    danser_renderer.fetch_beatmap_osz's docstring for why — the worker's own
    outbound internet is bandwidth-limited and can't reliably pull the file
    itself). When omitted, the worker falls back to fetching it itself.

    Returns (mp4_path, width, height, duration). The caller owns mp4_path and
    must delete it.

    Raises:
        RenderQueueFullError on HTTP 429.
        DanserError on any other non-200 response.
        RenderWorkerUnreachable on connection/timeout failures.
    """
    url = RENDER_WORKER_URL.rstrip("/") + "/render"
    headers = {"Authorization": f"Bearer {RENDER_WORKER_SECRET}"}
    # No total cap (marathons can run for many minutes); sock_connect catches an
    # unreachable worker. sock_read must exceed the whole silent render+fit, so it
    # comes from config (default 30 min) — 300s was too short and dropped good
    # renders mid-flight.
    timeout = aiohttp.ClientTimeout(
        total=None, sock_connect=10, sock_read=RENDER_WORKER_READ_TIMEOUT,
    )

    form = aiohttp.FormData()
    form.add_field(
        "replay", replay_bytes,
        filename="replay.osr", content_type="application/octet-stream",
    )
    if beatmapset_id is not None:
        form.add_field("beatmapset_id", str(beatmapset_id))
    if settings is not None:
        form.add_field("settings", json.dumps(settings), content_type="application/json")
    if beatmap_osz is not None:
        form.add_field(
            "beatmap_osz", beatmap_osz,
            filename="beatmap.osz", content_type="application/octet-stream",
        )

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form, headers=headers) as resp:
                if resp.status == 429:
                    raise RenderQueueFullError("Очередь рендеров заполнена. Попробуйте позже.")
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    raise DanserError(f"Render worker error {resp.status}: {body}")

                def _int_header(name: str) -> Optional[int]:
                    try:
                        return int(resp.headers.get(name) or 0) or None
                    except ValueError:
                        return None

                w = _int_header("X-Video-Width")
                h = _int_header("X-Video-Height")
                d = _int_header("X-Video-Duration")

                fd, mp4_path = tempfile.mkstemp(prefix="remote_render_", suffix=".mp4")
                with os.fdopen(fd, "wb") as f:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        f.write(chunk)
                return mp4_path, w, h, d
    except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
        raise RenderWorkerUnreachable(f"Render worker unreachable: {e}")
    except aiohttp.ClientError as e:
        raise RenderWorkerUnreachable(f"Render worker transport error: {e}")


async def delete_skin_remote(name: str) -> None:
    """Delete a skin folder on the worker. Raises DanserError / RenderWorkerUnreachable."""
    url = RENDER_WORKER_URL.rstrip("/") + "/skins/delete"
    headers = {"Authorization": f"Bearer {RENDER_WORKER_SECRET}"}
    timeout = aiohttp.ClientTimeout(total=30, sock_connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json={"name": name}, headers=headers) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    raise DanserError(f"Skin delete error {resp.status}: {body}")
    except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
        raise RenderWorkerUnreachable(f"Render worker unreachable: {e}")
    except aiohttp.ClientError as e:
        raise RenderWorkerUnreachable(f"Render worker transport error: {e}")


async def rename_skin_remote(name: str, new_name: str) -> str:
    """Rename a skin folder on the worker. Returns the sanitized new name
    actually used. Raises DanserError / RenderWorkerUnreachable."""
    url = RENDER_WORKER_URL.rstrip("/") + "/skins/rename"
    headers = {"Authorization": f"Bearer {RENDER_WORKER_SECRET}"}
    timeout = aiohttp.ClientTimeout(total=30, sock_connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, json={"name": name, "new_name": new_name}, headers=headers,
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    raise DanserError(f"Skin rename error {resp.status}: {body}")
                data = await resp.json()
                return data.get("name") or new_name
    except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
        raise RenderWorkerUnreachable(f"Render worker unreachable: {e}")
    except aiohttp.ClientError as e:
        raise RenderWorkerUnreachable(f"Render worker transport error: {e}")


async def install_skin_remote(osk_bytes: bytes, name: str) -> str:
    """Upload an .osk to the worker, which unpacks it into danser's Skins dir.
    Returns the installed skin name. Raises DanserError / RenderWorkerUnreachable."""
    url = RENDER_WORKER_URL.rstrip("/") + "/skins"
    headers = {"Authorization": f"Bearer {RENDER_WORKER_SECRET}"}
    timeout = aiohttp.ClientTimeout(total=120, sock_connect=10)
    form = aiohttp.FormData()
    form.add_field("skin", osk_bytes, filename="skin.osk", content_type="application/octet-stream")
    form.add_field("name", name)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form, headers=headers) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    raise DanserError(f"Skin upload error {resp.status}: {body}")
                data = await resp.json()
                return data.get("name") or name
    except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
        raise RenderWorkerUnreachable(f"Render worker unreachable: {e}")
    except aiohttp.ClientError as e:
        raise RenderWorkerUnreachable(f"Render worker transport error: {e}")
