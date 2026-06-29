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

from config.settings import RENDER_WORKER_URL, RENDER_WORKER_SECRET
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
) -> Tuple[str, Optional[int], Optional[int], Optional[int]]:
    """POST the replay to the worker and stream the mp4 to a temp file.

    Returns (mp4_path, width, height, duration). The caller owns mp4_path and
    must delete it.

    Raises:
        RenderQueueFullError on HTTP 429.
        DanserError on any other non-200 response.
        RenderWorkerUnreachable on connection/timeout failures.
    """
    url = RENDER_WORKER_URL.rstrip("/") + "/render"
    headers = {"Authorization": f"Bearer {RENDER_WORKER_SECRET}"}
    # No total cap (marathons can run for many minutes); sock_connect/read catch
    # an unreachable or stalled worker.
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=300)

    form = aiohttp.FormData()
    form.add_field(
        "replay", replay_bytes,
        filename="replay.osr", content_type="application/octet-stream",
    )
    if beatmapset_id is not None:
        form.add_field("beatmapset_id", str(beatmapset_id))
    if settings is not None:
        form.add_field("settings", json.dumps(settings), content_type="application/json")

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
