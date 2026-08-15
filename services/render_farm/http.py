"""The endpoints a render worker talks to.

Hung on the aiohttp application the OAuth callback already runs on, which binds
127.0.0.1 with Caddy in front — so these inherit its HTTPS and its hostname
rather than opening a second listener with a second thing to get wrong. Caddy
needs a route for `/render/*` pointed at the same upstream; without it these
answer only from the server itself.

Authentication is one shared secret in a bearer header, compared in constant
time. There is no user model here and there should not be: a worker is a
machine the operator runs, not an account. When the secret is unset the routes
are not registered at all — an unauthenticated render endpoint would accept a
job description from anyone and hand back somebody's replay.
"""

import json
import os
import secrets
import tempfile
from typing import Optional

from aiohttp import web

from config.settings import RENDER_WORKER_TOKEN
from services.render_farm.queue import RenderQueue, queue as default_queue
from utils.logger import get_logger

logger = get_logger("services.render_farm.http")

# A rendered video, streamed in. The ceiling is what a self-hosted Bot API will
# send; past it the upload is refused rather than buffered into memory.
MAX_RESULT_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK = 1 << 20


def _authorised(request: web.Request) -> bool:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix) or not RENDER_WORKER_TOKEN:
        return False
    return secrets.compare_digest(header[len(prefix):], RENDER_WORKER_TOKEN)


def _worker(request: web.Request) -> str:
    return request.headers.get("X-Render-Worker", "").strip()


def make_routes(queue: Optional[RenderQueue] = None) -> list[web.RouteDef]:
    """The route table, over `queue`. Parameterised for the tests, which run it
    against a queue of their own rather than the process-wide one."""
    q = queue if queue is not None else default_queue

    async def guard(request: web.Request) -> Optional[web.Response]:
        if not _authorised(request):
            return web.json_response({"error": "unauthorised"}, status=401)
        if not _worker(request):
            return web.json_response({"error": "no worker name"}, status=400)
        return None

    async def claim(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        job = q.claim(_worker(request))
        if job is None:
            # Not an error and not an empty job: there is simply nothing to do,
            # which a polling worker asks about constantly.
            return web.Response(status=204)
        return web.json_response({
            "id": job.id,
            "title": job.title,
            "settings": job.settings,
            # Names only. What each one is on this host is never sent, and a
            # name the worker invents matches nothing.
            "assets": sorted(job.assets),
            "lease_seconds": max(0.0, job.lease_until - job.created),
        })

    async def replay(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        job = q.get(request.match_info["job_id"])
        if job is None or job.worker != _worker(request):
            return web.json_response({"error": "not yours"}, status=409)
        if not os.path.isfile(job.replay_path):
            return web.json_response({"error": "replay is gone"}, status=410)
        return web.FileResponse(job.replay_path)

    async def asset(request: web.Request) -> web.Response:
        """One of the pictures the render needs, by the name the job gave it.

        A dictionary lookup rather than a path join, so there is no traversal
        to defend against: a name that is not one we offered is simply not a
        file, whatever it is spelled like.
        """
        bad = await guard(request)
        if bad:
            return bad
        job = q.get(request.match_info["job_id"])
        if job is None or job.worker != _worker(request):
            return web.json_response({"error": "not yours"}, status=409)
        path = job.assets.get(request.match_info["name"])
        if not path or not os.path.isfile(path):
            return web.json_response({"error": "no such asset"}, status=404)
        return web.FileResponse(path)

    async def heartbeat(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        alive = q.heartbeat(request.match_info["job_id"], _worker(request),
                            body.get("progress"))
        # 409 rather than 404: the job may well exist, just not for this worker
        # any more, and the worker's correct response to both is to stop.
        return web.json_response({"yours": alive}, status=200 if alive else 409)

    async def result(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        job_id, worker = request.match_info["job_id"], _worker(request)
        # Checked before a byte is read: an upload for a job this worker no
        # longer holds is a gigabyte written to a disk for nothing.
        if not q.heartbeat(job_id, worker):
            return web.json_response({"error": "not yours"}, status=409)

        handle, path = tempfile.mkstemp(prefix="render-result-", suffix=".mp4")
        written = 0
        try:
            with os.fdopen(handle, "wb") as out:
                async for chunk in request.content.iter_chunked(_CHUNK):
                    written += len(chunk)
                    if written > MAX_RESULT_BYTES:
                        raise ValueError("result too large")
                    out.write(chunk)
        except (ValueError, OSError) as exc:
            os.unlink(path)
            logger.warning("result upload for %s failed: %s", job_id, exc)
            return web.json_response({"error": str(exc)}, status=413)

        meta = {}
        raw = request.headers.get("X-Render-Meta")
        if raw:
            try:
                meta = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                logger.warning("job %s sent unreadable meta", job_id)

        # The last word on ownership. Between the check above and now the lease
        # could have expired and the bot could have started rendering it here;
        # dropping the file is the cheap half of that race.
        if not q.finish(job_id, worker, {"path": path, "meta": meta}):
            os.unlink(path)
            return web.json_response({"error": "not yours"}, status=409)
        return web.json_response({"ok": True})

    async def give_back(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        given = q.give_back(request.match_info["job_id"], _worker(request),
                            str(body.get("reason") or "no reason given"))
        return web.json_response({"ok": given}, status=200 if given else 409)

    return [
        web.post("/render/claim", claim),
        web.get("/render/job/{job_id}/replay", replay),
        web.get("/render/job/{job_id}/file/{name}", asset),
        web.post("/render/job/{job_id}/heartbeat", heartbeat),
        web.post("/render/job/{job_id}/result", result),
        web.post("/render/job/{job_id}/give-back", give_back),
    ]


def install(app: web.Application, queue: Optional[RenderQueue] = None) -> bool:
    """Register the worker endpoints, unless there is no secret to guard them.

    Returns whether they were installed, so the caller can say so at startup —
    a remote render that silently never happens is hard to tell from a laptop
    that is merely asleep.
    """
    if not RENDER_WORKER_TOKEN:
        logger.info("no RENDER_WORKER_TOKEN: renders stay on this host")
        return False
    app.add_routes(make_routes(queue))
    logger.info("render worker endpoints ready")
    return True
