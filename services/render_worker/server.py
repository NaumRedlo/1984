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

# Caps the REQUEST body. The .osr is tiny, but a custom skin (.osk) upload can be
# tens of MB, so allow up to 96 MB. Does NOT cap the streamed mp4 RESPONSE.
_MAX_REQUEST_BYTES = 96 * 1024 * 1024


def _check_auth(request: web.Request) -> bool:
    """Constant-time Bearer check. Fails closed when no secret is configured."""
    if not RENDER_WORKER_SECRET:
        return False
    provided = request.headers.get("Authorization", "")
    return hmac.compare_digest(provided, f"Bearer {RENDER_WORKER_SECRET}")


async def handle_health(request: web.Request) -> web.Response:
    """Unauthenticated liveness probe (the firewall is the privacy boundary).
    gl_ready additionally confirms GLX can actually hand out a context right
    now (not just that this process is listening) — see _check_gl_ready's
    docstring for the 2026-07-03 incident that motivated it."""
    return web.json_response({
        "status": "ok",
        "gl_ready": await dr._check_gl_ready(),
        "inflight": dr.core._inflight,
        "max_queue": dr.core._MAX_QUEUE,
    })


async def handle_list_skins(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({"skins": dr.list_skins()})


async def handle_install_skin(request: web.Request) -> web.Response:
    """Install a custom skin: multipart with an .osk file (`skin`) + `name`."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not (request.content_type or "").startswith("multipart/"):
        return web.json_response({"error": "expected multipart/form-data"}, status=400)

    osk_bytes = None
    name = ""
    reader = await request.multipart()
    async for part in reader:
        if part.name == "skin":
            osk_bytes = await part.read(decode=False)
        elif part.name == "name":
            name = (await part.text()).strip()

    if not osk_bytes:
        return web.json_response({"error": "missing skin file"}, status=400)
    try:
        installed = dr.install_skin(osk_bytes, name)
    except dr.DanserError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True, "name": installed})


async def handle_delete_skin(request: web.Request) -> web.Response:
    """Delete a skin folder: JSON body {"name": ...}."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "expected JSON body"}, status=400)
    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "missing name"}, status=400)
    try:
        dr.delete_skin(name)
    except dr.DanserError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True})


async def handle_rename_skin(request: web.Request) -> web.Response:
    """Rename a skin folder: JSON body {"name": ..., "new_name": ...}."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "expected JSON body"}, status=400)
    name = (body.get("name") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    if not name or not new_name:
        return web.json_response({"error": "missing name/new_name"}, status=400)
    try:
        installed = dr.rename_skin(name, new_name)
    except dr.DanserError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True, "name": installed})


async def handle_render(request: web.Request) -> web.StreamResponse:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    replay_bytes: Optional[bytes] = None
    beatmapset_id: Optional[int] = None
    settings: Optional[dict] = None
    beatmap_osz: Optional[bytes] = None

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
        elif part.name == "beatmap_osz":
            beatmap_osz = await part.read(decode=False)

    if not replay_bytes:
        return web.json_response({"error": "missing replay"}, status=400)

    try:
        dr._check_danser()
    except dr.DanserNotFoundError as e:
        logger.error("danser not available: %s", e)
        return web.json_response({"error": str(e)}, status=503)

    # 2026-07-04: the bot now fetches the .osz itself and hands the bytes over
    # here (beatmap_osz) instead of this worker downloading it — this box's
    # outbound internet goes through a bandwidth-limited proxy that stalls on
    # files this size (see fetch_beatmap_osz's docstring). Falls back to the
    # worker fetching it itself only if the bot didn't send bytes (an older
    # bot not yet redeployed, or beatmapset_id present without bytes).
    if beatmap_osz and beatmapset_id:
        if not dr.save_beatmap_osz(beatmapset_id, beatmap_osz):
            return web.json_response({"error": "beatmap bytes invalid"}, status=502)
    elif beatmapset_id and not await dr.download_beatmap(beatmapset_id):
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
        try:
            await resp.prepare(request)
            with open(video_path, "rb") as vf:
                while True:
                    chunk = vf.read(256 * 1024)
                    if not chunk:
                        break
                    await resp.write(chunk)
            await resp.write_eof()
        except (ConnectionResetError, ConnectionError) as e:
            # The bot gave up (e.g. read timeout) before we finished — log cleanly
            # instead of a full traceback. The finally below still cleans up.
            logger.warning("client disconnected while sending render: %s", e)
            return resp
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
        self.app.router.add_get("/skins", handle_list_skins)
        self.app.router.add_post("/skins", handle_install_skin)
        self.app.router.add_post("/skins/delete", handle_delete_skin)
        self.app.router.add_post("/skins/rename", handle_rename_skin)
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
