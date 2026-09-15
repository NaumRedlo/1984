import json
import os
import secrets
import tempfile
from typing import Optional

from aiohttp import web

from config.settings import RENDER_WORKER_TOKEN
from dossier import build as engine_build
from services.render_farm.queue import RenderQueue, queue as default_queue
from services.render_farm import invites, pairing
from services.render_farm.roster import Roster, roster as default_roster
from utils.logger import get_logger

logger = get_logger("services.render_farm.http")

MAX_RESULT_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK = 1 << 20

def _authorised(request: web.Request) -> bool:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    offered = header[len(prefix):]
    if RENDER_WORKER_TOKEN and secrets.compare_digest(offered, RENDER_WORKER_TOKEN):
        return True
    return invites.known(offered)

_release_cache: Optional[str] = None

def _release() -> str:
    global _release_cache
    if _release_cache is None:
        try:
            from scripts.engine import wanted_tag

            _release_cache = wanted_tag()
        except Exception as exc:
            logger.warning("cannot say which release this bot is on: %s", exc)
            _release_cache = ""
    return _release_cache

def _worker(request: web.Request) -> str:
    return request.headers.get("X-Render-Worker", "").strip()

def _address(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "?"
    return request.remote or "?"

def make_routes(queue: Optional[RenderQueue] = None,
                roster: Optional[Roster] = None) -> list[web.RouteDef]:
    q = queue if queue is not None else default_queue
    who = roster if roster is not None else default_roster

    async def guard(request: web.Request) -> Optional[web.Response]:
        if not _authorised(request):
            return web.json_response({"error": "unauthorised"}, status=401)
        if not _worker(request):
            return web.json_response({"error": "no worker name"}, status=400)
        return None

    async def hello(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        ours = await engine_build.local()
        theirs = request.query.get("engine")
        who.hello(_worker(request), build=theirs or None)
        allowed, why = engine_build.agree(ours, theirs)
        return web.json_response({
            "engine": ours,
            "build": engine_build.build_of(ours),
            "agree": allowed,
            "reason": why,
            "waiting": len(q.waiting()),

            "release": _release(),
        })

    async def claim(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad

        theirs = None
        capacity = None
        if request.can_read_body:
            body = await request.json()
            if isinstance(body, dict):
                theirs = body.get("engine")
                capacity = body.get("capacity")
        who.hello(_worker(request), build=theirs, capacity=capacity)

        if capacity is not None and not capacity.get("take", True):
            return web.Response(status=204)

        ours = await engine_build.local()
        allowed, why = engine_build.agree(ours, theirs)
        if not allowed:
            logger.warning("refused %s: %s", _worker(request), why)

            return web.json_response({"reason": why, "release": _release()}, status=409)

        job = q.claim(_worker(request))
        if job is None:

            return web.Response(status=204)
        return web.json_response({
            "id": job.id,
            "title": job.title,
            "settings": job.settings,

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
        who.hello(_worker(request))
        alive = q.heartbeat(request.match_info["job_id"], _worker(request),
                            body.get("progress"))

        return web.json_response({"yours": alive}, status=200 if alive else 409)

    async def result(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        job_id, worker = request.match_info["job_id"], _worker(request)
        who.hello(worker)

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

        if not q.finish(job_id, worker, {"path": path, "meta": meta}):
            os.unlink(path)
            return web.json_response({"error": "not yours"}, status=409)
        who.delivered(worker)
        return web.json_response({"ok": True})

    async def give_back(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        who.hello(_worker(request))
        given = q.give_back(request.match_info["job_id"], _worker(request),
                            str(body.get("reason") or "no reason given"))
        if given:
            who.handed_back(_worker(request))
        return web.json_response({"ok": given}, status=200 if given else 409)

    async def farm(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        busy = q.rendering()
        return web.json_response({
            "waiting": len(q.waiting()),
            "workers": [{
                "name": w.name,
                "state": w.state(rendering=w.name in busy),
                "build": engine_build.build_of(w.build),
                "reason": w.reason,
                "threads": w.threads,
                "polite": w.polite,
                "delivered": w.delivered,
                "handed_back": w.handed_back,
            } for w in who.here()],
        })

    async def join(request: web.Request) -> web.Response:
        try:
            said = await request.json()
        except Exception:
            return web.json_response({"error": "bad request"}, status=400)

        invite = invites.redeem(str(said.get("code", "")))
        if invite is None:

            return web.json_response({"error": "no such code"}, status=403)

        token = await invites.issue(invite, str(said.get("name", ""))[:128])
        return web.json_response({"token": token})

    async def pair(request: web.Request) -> web.Response:
        try:
            said = await request.json()
        except Exception:
            return web.json_response({"error": "bad request"}, status=400)
        if not isinstance(said, dict):
            return web.json_response({"error": "bad request"}, status=400)
        name = str(said.get("name") or "").strip()[:128]
        if not name:
            return web.json_response({"error": "no machine name"}, status=400)
        try:
            cores = max(0, int(said.get("cores") or 0))
        except (TypeError, ValueError):
            cores = 0
        machine = pairing.Machine(
            name=name,
            os=str(said.get("os") or "").strip()[:64],
            cores=cores,
            build=str(said.get("build") or "").strip()[:64],
        )
        code = pairing.start(machine, _address(request))
        if code is None:
            return web.json_response({"error": "too many"}, status=429)
        return web.json_response({
            "code": invites.pretty(code),
            "link": pairing.link(code),
            "expires_in": int(pairing.GOOD_FOR),
        })

    async def pair_status(request: web.Request) -> web.Response:
        got = await pairing.collect(request.match_info["code"], _address(request))
        if got.status == pairing.THROTTLED:
            return web.json_response({"error": "too many"}, status=429)
        if got.status == pairing.GONE:
            return web.json_response({"status": pairing.GONE}, status=404)
        body = {"status": got.status}
        if got.token:
            body["token"] = got.token
        return web.json_response(body)

    return [
        web.post("/render/join", join),
        web.post("/render/pair", pair),
        web.get("/render/pair/{code}", pair_status),
        web.get("/render/hello", hello),
        web.get("/render/farm", farm),
        web.post("/render/claim", claim),
        web.get("/render/job/{job_id}/replay", replay),
        web.get("/render/job/{job_id}/file/{name}", asset),
        web.post("/render/job/{job_id}/heartbeat", heartbeat),
        web.post("/render/job/{job_id}/result", result),
        web.post("/render/job/{job_id}/give-back", give_back),
    ]

def install(app: web.Application, queue: Optional[RenderQueue] = None) -> bool:
    if not RENDER_WORKER_TOKEN:
        logger.info("no RENDER_WORKER_TOKEN: renders stay on this host")
        return False
    app.add_routes(make_routes(queue))
    logger.info("render worker endpoints ready")
    return True
