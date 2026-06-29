"""aiohttp render-worker service.

Exposes POST /render (multipart: replay file + beatmapset_id + settings JSON) and
GET /health. Reuses utils/osu/danser_renderer verbatim — no rendering logic is
duplicated here. Auth is a shared Bearer secret; the worker port must be
firewalled to the bot's IP (the secret travels in plaintext over HTTP in v1).
"""

import hmac
import json
import os
import shutil
import tempfile
import time
from typing import Optional

from aiohttp import web

from config.settings import (
    RENDER_FIT_MAX_MB,
    RENDER_GPU,
    RENDER_WORKER_BIND,
    RENDER_WORKER_PORT,
    RENDER_WORKER_SECRET,
)
from utils.logger import get_logger
from utils.osu import danser_renderer as dr

logger = get_logger("render_worker")

# The .osr upload is small (KBs–low MB); cap the REQUEST body to reject junk.
# This does NOT cap the streamed mp4 RESPONSE, which can be tens of MB.
_MAX_REQUEST_BYTES = 8 * 1024 * 1024


def _check_auth(request: web.Request) -> bool:
    """Constant-time Bearer check. Fails closed when no secret is configured."""
    if not RENDER_WORKER_SECRET:
        return False
    provided = request.headers.get("Authorization", "")
    return hmac.compare_digest(provided, f"Bearer {RENDER_WORKER_SECRET}")


async def handle_health(request: web.Request) -> web.Response:
    """Unauthenticated liveness probe (the firewall is the privacy boundary)."""
    return web.json_response({
        "status": "ok",
        "inflight": dr._inflight,
        "max_queue": dr._MAX_QUEUE,
    })


async def handle_render(request: web.Request) -> web.StreamResponse:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    replay_bytes: Optional[bytes] = None
    beatmapset_id: Optional[int] = None
    settings: Optional[dict] = None

    if not (request.content_type or "").startswith("multipart/"):
        return web.json_response({"error": "expected multipart/form-data"}, status=400)

    reader = await request.multipart()
    async for part in reader:
        if part.name == "replay":
            replay_bytes = await part.read(decode=False)
        elif part.name == "beatmapset_id":
            raw = (await part.text()).strip()
            beatmapset_id = int(raw) if raw.isdigit() else None
        elif part.name == "settings":
            txt = (await part.text()).strip()
            settings = json.loads(txt) if txt else None

    if not replay_bytes:
        return web.json_response({"error": "missing replay"}, status=400)

    try:
        dr._check_danser()
    except dr.DanserNotFoundError as e:
        logger.error("danser not available: %s", e)
        return web.json_response({"error": str(e)}, status=503)

    if beatmapset_id and not await dr.download_beatmap(beatmapset_id):
        return web.json_response({"error": "beatmap download failed"}, status=502)

    tmp_dir = tempfile.mkdtemp(prefix="rworker_")
    video_path: Optional[str] = None
    try:
        osr_path = os.path.join(tmp_dir, "replay.osr")
        with open(osr_path, "wb") as f:
            f.write(replay_bytes)

        out_name = f"render_{int(time.time())}_{os.getpid()}"
        try:
            # Progress callbacks are dropped in v1 (no live % over HTTP); the
            # FIFO queue / RenderQueueFullError inside render_replay is preserved.
            video_path = await dr.render_replay(
                replay_path=osr_path,
                output_path=out_name,
                settings=settings,
                on_progress=None,
                on_queue=None,
            )
        except dr.RenderQueueFullError:
            return web.json_response({"error": "queue full"}, status=429)
        except dr.DanserError as e:
            logger.error("render failed: %s", e)
            return web.json_response({"error": str(e)}, status=500)

        # Squeeze 1080p60 under the cap when needed (NVENC in GPU mode). Re-probe
        # afterwards since a re-encode changes the file (and may have new dims).
        if RENDER_FIT_MAX_MB > 0:
            video_path = await dr.fit_video_to_size(
                video_path, RENDER_FIT_MAX_MB * 1024 * 1024, gpu=RENDER_GPU,
            )

        w, h, d = await dr.probe_video(video_path)
        size = os.path.getsize(video_path)
        resp = web.StreamResponse(status=200, headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
            "X-Video-Width": str(w or ""),
            "X-Video-Height": str(h or ""),
            "X-Video-Duration": str(d or ""),
        })
        await resp.prepare(request)
        with open(video_path, "rb") as vf:
            while True:
                chunk = vf.read(256 * 1024)
                if not chunk:
                    break
                await resp.write(chunk)
        await resp.write_eof()
        logger.info("served render: %s bytes (%sx%s)", size, w, h)
        return resp
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if video_path and os.path.isfile(video_path):
            try:
                os.remove(video_path)
            except Exception:
                pass


class RenderWorkerServer:
    def __init__(self, host: str = RENDER_WORKER_BIND, port: int = RENDER_WORKER_PORT):
        self.host = host
        self.port = port
        self.app = web.Application(client_max_size=_MAX_REQUEST_BYTES)
        self.app.router.add_post("/render", handle_render)
        self.app.router.add_get("/health", handle_health)
        self.runner: Optional[web.AppRunner] = None

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        logger.info(f"Render worker started on {self.host}:{self.port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            logger.info("Render worker stopped")
