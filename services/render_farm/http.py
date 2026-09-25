import json
import os
import secrets
import tempfile
from typing import Optional

from aiogram import Bot, types
from aiohttp import web

from config.settings import RENDER_WORKER_TOKEN, TRUSTED_PROXY_HOPS
from services.render_farm import community as gathered, invites, pairing
from services.render_farm.queue import RenderQueue, queue as default_queue
from services.render_farm.roster import Roster, roster as default_roster
from utils.logger import get_logger
from utils.ttl_cache import TTLCache

logger = get_logger("services.render_farm.http")

_CHUNK = 1 << 20

_bot: Optional[Bot] = None
_osu = None

def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot

def set_osu(client) -> None:
    global _osu
    _osu = client

def _token(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    return header[len(prefix):] if header.startswith(prefix) else ""

def _max_send_bytes() -> int:
    from config.settings import TELEGRAM_BOT_API_URL

    if TELEGRAM_BOT_API_URL:
        return 2000 * 1024 * 1024
    return 48 * 1024 * 1024

async def _spool(request: web.Request, most: int, prefix: str) -> tuple[str, int]:
    handle, path = tempfile.mkstemp(prefix=prefix, suffix=".mp4")
    written = 0
    try:
        with os.fdopen(handle, "wb") as out:
            async for chunk in request.content.iter_chunked(_CHUNK):
                written += len(chunk)
                if written > most:
                    raise ValueError("too large")
                out.write(chunk)
    except (ValueError, OSError):
        os.unlink(path)
        raise
    return path, written

def _authorised(request: web.Request) -> bool:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    offered = header[len(prefix):]
    if RENDER_WORKER_TOKEN and secrets.compare_digest(offered, RENDER_WORKER_TOKEN):
        return True
    return invites.known(offered)

CARD_KEEP = 300.0
_card_cache = TTLCache(maxsize=500, ttl=CARD_KEEP)

def _plain(value):
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

async def _card_of(user, session, handle: Optional[str], viewer: int) -> dict:
    from bot.handlers.profile.handlers import _build_page_data

    data = await _build_page_data(user, _osu, session, tg_handle=handle, viewer_tg_id=viewer)
    data["play_seconds"] = int(getattr(user, "play_time", 0) or 0)
    data["title_code"] = getattr(user, "active_title_code", None)
    return _plain(data)

FRIENDS_URL = "https://osu.ppy.sh/api/v2/friends"
FRIENDS_KEEP = 90.0
_friends_cache = TTLCache(maxsize=500, ttl=FRIENDS_KEEP)

async def _scopes(telegram_id: int) -> Optional[str]:
    from sqlalchemy import select

    from db.database import AsyncSessionFactory
    from db.models.oauth_token import OAuthToken

    async with AsyncSessionFactory() as session:
        row = (await session.execute(
            select(OAuthToken.scopes).where(OAuthToken.telegram_id == telegram_id)
        )).first()
    if row is None:
        return None
    return row[0] or ""

async def _osu_friends(token: str) -> Optional[list]:
    import aiohttp

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "x-api-version": "20220705"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
            async with client.get(FRIENDS_URL, headers=headers) as reply:
                if reply.status != 200:
                    logger.info("osu! answered %s for a friends list", reply.status)
                    return None
                return await reply.json()
    except Exception as exc:
        logger.warning("cannot ask osu! for friends: %s", exc)
        return None

def _worker(request: web.Request) -> str:
    return request.headers.get("X-Render-Worker", "").strip()

def _address(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        hops = [part.strip() for part in forwarded.split(",")]
        return hops[-min(TRUSTED_PROXY_HOPS, len(hops))] or "?"
    return request.remote or "?"

async def _json_object(request: web.Request) -> dict:
    try:
        said = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return {}
    return said if isinstance(said, dict) else {}

def _meta(request: web.Request) -> dict:
    raw = request.headers.get("X-Render-Meta")
    if not raw:
        return {}
    try:
        meta = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}

def _dimension(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return int(value) if 0 < value < 1 << 31 else None

def _key(request: web.Request) -> str:
    return f"{invites.digest(_token(request))[:16]}:{_worker(request)}"

def make_routes(queue: Optional[RenderQueue] = None, roster: Optional[Roster] = None) -> list[web.RouteDef]:
    line = queue if queue is not None else default_queue
    who = roster if roster is not None else default_roster

    async def guard(request: web.Request) -> Optional[web.Response]:
        if not _authorised(request):
            return web.json_response({"error": "unauthorised"}, status=401)
        if not _worker(request):
            return web.json_response({"error": "no worker name"}, status=400)
        return None

    def _seen(request: web.Request, *, build: Optional[str] = None, take: Optional[bool] = None) -> str:
        key = _key(request)
        owner = invites.owner(_token(request))
        who.hello(key, _worker(request)[:128], owner.telegram_id if owner else 0, build=build, take=take)
        return key

    def _mine(request: web.Request):
        job = line.get(request.match_info["job_id"])
        if job is None or job.worker != _key(request):
            return None
        return job

    async def hello(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        return web.json_response({"build": "", "agree": True, "reason": "", "waiting": len(line.waiting()), "most": _max_send_bytes()})

    async def claim(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        said = await _json_object(request) if request.can_read_body else {}
        build = said.get("build") if isinstance(said.get("build"), str) else None
        take = said.get("take")
        take = bool(take) if isinstance(take, bool) else True
        key = _seen(request, build=build, take=take)
        if not take:
            return web.Response(status=204)
        job = line.claim(key, _worker(request)[:128])
        if job is None:
            return web.Response(status=204)
        handed = job.handed()
        handed["most"] = _max_send_bytes()
        return web.json_response(handed)

    async def job_replay(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        job = _mine(request)
        if job is None:
            return web.json_response({"error": "not yours"}, status=409)
        if not os.path.isfile(job.replay_path):
            return web.json_response({"error": "replay is gone"}, status=410)
        return web.FileResponse(job.replay_path)

    async def job_skin(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        job = _mine(request)
        if job is None:
            return web.json_response({"error": "not yours"}, status=409)
        path = (job.skin or {}).get("path")
        if not path or not os.path.isfile(path):
            return web.json_response({"error": "no skin"}, status=404)
        return web.FileResponse(path)

    async def job_heartbeat(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        said = await _json_object(request)
        _seen(request)
        progress = said.get("progress")
        alive = line.heartbeat(request.match_info["job_id"], _key(request), progress if isinstance(progress, dict) else None)
        return web.json_response({"yours": alive}, status=200 if alive else 409)

    async def job_result(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        key = _seen(request)
        job_id = request.match_info["job_id"]
        if not line.heartbeat(job_id, key):
            return web.json_response({"error": "not yours"}, status=409)
        try:
            path, written = await _spool(request, _max_send_bytes(), "render-result-")
        except ValueError:
            line.give_back(job_id, key, "the video was too large to send")
            who.handed_back(key)
            return web.json_response({"error": "too large", "most": _max_send_bytes()}, status=413)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        if not line.finish(job_id, key, {"path": path, "meta": _meta(request), "bytes": written}):
            os.unlink(path)
            return web.json_response({"error": "not yours"}, status=409)
        who.delivered(key)
        return web.json_response({"ok": True})

    async def job_give_back(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        said = await _json_object(request)
        key = _seen(request)
        given = line.give_back(request.match_info["job_id"], key, str(said.get("reason") or "no reason given")[:300])
        if given:
            who.handed_back(key)
        return web.json_response({"ok": given}, status=200 if given else 409)

    async def farm(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        mine = _key(request)
        busy = line.rendering()
        workers = []
        for worker in who.here():
            job = busy.get(worker.key)
            state = "rendering" if job else ("ready" if worker.take else "resting")
            workers.append({
                "name": worker.name,
                "state": state,
                "delivered": worker.delivered,
                "handed_back": worker.handed_back,
                "mine": worker.key == mine,
                "progress": (job.progress or {}) if job else None,
            })
        return web.json_response({"waiting": len(line.waiting()), "workers": workers})

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
            body["who"] = got.who
        return web.json_response(body)

    async def me(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        body = {"telegram_id": owner.telegram_id, "name": owner.name, "username": "", "avatar": False}
        if _bot is not None:
            try:
                chat = await _bot.get_chat(owner.telegram_id)
                body["username"] = chat.username or ""
                body["name"] = " ".join(p for p in (chat.first_name, chat.last_name) if p) or owner.name
                body["avatar"] = chat.photo is not None
            except Exception as exc:
                logger.warning("cannot describe %s: %s", owner.telegram_id, exc)
        return web.json_response(body)

    async def me_avatar(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None or _bot is None:
            return web.json_response({"error": "no one"}, status=404)
        try:
            chat = await _bot.get_chat(owner.telegram_id)
            if chat.photo is None:
                return web.json_response({"error": "no photo"}, status=404)
            file = await _bot.get_file(chat.photo.small_file_id)
            buffer = await _bot.download_file(file.file_path)
            data = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
        except Exception as exc:
            logger.warning("cannot fetch the photo of %s: %s", owner.telegram_id, exc)
            return web.json_response({"error": "no photo"}, status=404)
        return web.Response(body=data, content_type="image/jpeg")

    async def chats(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        rows = [{"id": owner.telegram_id, "title": owner.name or "", "private": True, "photo": True}]
        seen = {owner.telegram_id}
        try:
            from sqlalchemy import select

            from db.database import AsyncSessionFactory
            from db.models import User

            async with AsyncSessionFactory() as session:
                found = await session.execute(
                    select(User.chat_id).where(User.telegram_id == owner.telegram_id).distinct()
                )
                where = [row[0] for row in found.all()]
        except Exception as exc:
            logger.warning("cannot read the chats of %s: %s", owner.telegram_id, exc)
            where = []
        for chat_id in where:
            if chat_id in seen:
                continue
            seen.add(chat_id)
            title = ""
            photo = False
            if _bot is not None:
                try:
                    chat = await _bot.get_chat(chat_id)
                    title = chat.title or chat.full_name or ""
                    photo = chat.photo is not None
                    if not await _member(chat_id, owner.telegram_id):
                        continue
                except Exception as exc:
                    logger.info("chat %s is not reachable: %s", chat_id, exc)
                    continue
            rows.append({"id": chat_id, "title": title, "private": chat_id > 0, "photo": photo})
        return web.json_response(rows)

    async def _member(chat_id: int, telegram_id: int) -> bool:
        if _bot is None:
            return False
        try:
            member = await _bot.get_chat_member(chat_id, telegram_id)
        except Exception:
            return False
        return getattr(member, "status", "left") not in {"left", "kicked"}

    async def chat_avatar(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None or _bot is None:
            return web.json_response({"error": "no one"}, status=404)
        try:
            chat_id = int(request.match_info["chat_id"])
        except (TypeError, ValueError):
            return web.json_response({"error": "no chat"}, status=400)
        if chat_id != owner.telegram_id and not await _member(chat_id, owner.telegram_id):
            return web.json_response({"error": "not your chat"}, status=403)
        try:
            chat = await _bot.get_chat(chat_id)
            if chat.photo is None:
                return web.json_response({"error": "no photo"}, status=404)
            file = await _bot.get_file(chat.photo.small_file_id)
            buffer = await _bot.download_file(file.file_path)
            data = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
        except Exception as exc:
            logger.info("no photo for chat %s: %s", chat_id, exc)
            return web.json_response({"error": "no photo"}, status=404)
        return web.Response(body=data, content_type="image/jpeg")

    async def _groups_of(telegram_id: int) -> list[int]:
        from sqlalchemy import select

        from db.database import AsyncSessionFactory
        from db.models import User

        async with AsyncSessionFactory() as session:
            found = await session.execute(
                select(User.chat_id).where(User.telegram_id == telegram_id, User.chat_id < 0).distinct()
            )
            return [row[0] for row in found.all()]

    async def _group_for(telegram_id: int, said: str) -> tuple[Optional[int], bool]:
        chat_id: Optional[int] = int(said) if said.lstrip("-").isdigit() else None
        if chat_id is None or chat_id > 0:
            for group in await _groups_of(telegram_id):
                if await _member(group, telegram_id):
                    return group, True
            return None, True
        return chat_id, await _member(chat_id, telegram_id)

    async def community(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        chat_id, allowed = await _group_for(owner.telegram_id, request.query.get("chat", ""))
        if not allowed:
            return web.json_response({"error": "not your chat"}, status=403)

        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await gathered.gather(session, chat_id, owner.telegram_id) if chat_id is not None else {
                "chat": None, "week": 0, "people": [], "live": [], "happened": [], "titles": gathered.titles_catalogue(), "at": None,
            }
            body["me"] = await gathered.own(session, owner.telegram_id, chat_id)
        body["group"] = ""
        body["photo"] = False
        if chat_id is not None and _bot is not None:
            try:
                chat = await _bot.get_chat(chat_id)
                body["group"] = chat.title or chat.full_name or ""
                body["photo"] = chat.photo is not None
            except Exception as exc:
                logger.info("chat %s is not reachable: %s", chat_id, exc)
        return web.json_response(body)

    async def someone(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        chat_said, user_said = request.query.get("chat", ""), request.query.get("id", "")
        if not chat_said.lstrip("-").isdigit() or not user_said.isdigit():
            return web.json_response({"error": "no one"}, status=400)
        chat_id, user_id = int(chat_said), int(user_said)
        if chat_id > 0 or not await _member(chat_id, owner.telegram_id):
            return web.json_response({"error": "not your chat"}, status=403)

        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await gathered.someone(session, user_id, chat_id, owner.telegram_id)
        if body is None:
            return web.json_response({"error": "no one"}, status=404)
        return web.json_response(body)

    async def share_card(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        if request.content_length is not None and request.content_length > gathered.APP_PROFILE_MOST:
            return web.json_response({"error": "too large"}, status=413)
        try:
            card = await request.json()
        except Exception:
            return web.json_response({"error": "not json"}, status=400)
        if not isinstance(card, dict):
            return web.json_response({"error": "not a card"}, status=400)

        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            try:
                kept = await gathered.keep_card(session, owner.telegram_id, card)
            except ValueError:
                return web.json_response({"error": "too large"}, status=413)
        return web.json_response({"kept": kept})

    async def card(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        if _osu is None:
            return web.json_response({"error": "osu! is not reachable"}, status=503)
        said = request.query.get("chat", "")
        chat_id: Optional[int] = int(said) if said.lstrip("-").isdigit() else None
        key = (owner.telegram_id, chat_id)
        kept = _card_cache.get(key)
        if kept is not None:
            return web.json_response(kept)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            user = await gathered.chosen(session, owner.telegram_id, chat_id)
            if user is None:
                return web.json_response({"error": "not registered"}, status=404)
            handle = None
            if _bot is not None:
                try:
                    chat = await _bot.get_chat(owner.telegram_id)
                    handle = f"@{chat.username}" if chat.username else None
                except Exception as exc:
                    logger.info("cannot learn the handle of %s: %s", owner.telegram_id, exc)
            try:
                body = await _card_of(user, session, handle, owner.telegram_id)
            except Exception as exc:
                logger.warning("the card of %s could not be gathered: %s", owner.telegram_id, exc)
                return web.json_response({"error": "no card"}, status=502)
        _card_cache[key] = body
        return web.json_response(body)

    async def played(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        from db.database import AsyncSessionFactory
        from tasks import live_tracker

        async with AsyncSessionFactory() as session:
            user = await gathered.chosen(session, owner.telegram_id)
        if user is None or not user.osu_user_id:
            return web.json_response({"error": "not registered"}, status=404)
        heard = live_tracker.nudge(int(user.osu_user_id))
        return web.json_response({"heard": heard}, status=202)

    async def wear_title(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        said = await _json_object(request)
        code = said.get("code")
        code = str(code) if code else None
        chat_id, allowed = await _group_for(owner.telegram_id, str(said.get("chat") or ""))
        if not allowed:
            return web.json_response({"error": "not your chat"}, status=403)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            verdict = await gathered.wear(session, owner.telegram_id, chat_id, code)
        if verdict == gathered.NOT_REGISTERED:
            return web.json_response({"error": "not registered"}, status=404)
        if verdict == gathered.NOT_UNLOCKED:
            return web.json_response({"error": "not unlocked"}, status=403)
        for key in ((owner.telegram_id, chat_id), (owner.telegram_id, None)):
            _card_cache.pop(key)
        return web.json_response({"title": code})

    async def friends(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        kept = _friends_cache.get(owner.telegram_id)
        if kept is not None:
            return web.json_response({"friends": kept})
        scopes = await _scopes(owner.telegram_id)
        if scopes is None:
            return web.json_response({"need": "link"}, status=409)
        if "friends.read" not in scopes.split():
            return web.json_response({"need": "friends"}, status=409)
        from services.oauth.token_manager import get_valid_token

        token = await get_valid_token(owner.telegram_id)
        if not token:
            return web.json_response({"need": "link"}, status=409)
        raw = await _osu_friends(token)
        if raw is None:
            return web.json_response({"error": "osu! did not answer"}, status=502)
        listed = gathered.friends_from(raw)
        _friends_cache[owner.telegram_id] = listed
        return web.json_response({"friends": listed})

    async def send(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = invites.owner(_token(request))
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        if _bot is None:
            return web.json_response({"error": "bot asleep"}, status=503)
        try:
            path, written = await _spool(request, _max_send_bytes(), "render-send-")
        except ValueError:
            return web.json_response({"error": "too large", "most": _max_send_bytes()}, status=413)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        meta = _meta(request)
        caption = str(meta.get("caption", ""))[:1024]
        where = meta.get("chat")
        where = int(where) if isinstance(where, (int, str)) and str(where).lstrip("-").isdigit() else owner.telegram_id
        if where != owner.telegram_id and not await _member(where, owner.telegram_id):
            os.unlink(path)
            return web.json_response({"error": "not your chat"}, status=403)
        filename = os.path.basename(str(meta.get("name") or "")).strip()[:128] or "render.mp4"
        try:
            sent = await _bot.send_video(
                where,
                types.FSInputFile(path, filename=filename),
                caption=caption or None,
                supports_streaming=True,
                width=_dimension(meta.get("width")),
                height=_dimension(meta.get("height")),
                duration=_dimension(meta.get("duration")),
            )
        except Exception as exc:
            logger.warning("sending a video to %s failed: %s", where, exc)
            return web.json_response({"error": str(exc)}, status=502)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        logger.info("a video of %d bytes went to %s", written, where)
        return web.json_response({"ok": True, "message_id": sent.message_id})

    return [
        web.post("/render/pair", pair),
        web.get("/render/pair/{code}", pair_status),
        web.get("/render/hello", hello),
        web.get("/render/me", me),
        web.get("/render/me/avatar", me_avatar),
        web.get("/render/me/chats", chats),
        web.get("/render/chat/{chat_id}/avatar", chat_avatar),
        web.get("/render/community", community),
        web.get("/render/community/person", someone),
        web.post("/render/me/profile", share_card),
        web.post("/render/me/title", wear_title),
        web.post("/render/me/played", played),
        web.get("/render/me/friends", friends),
        web.get("/render/me/card", card),
        web.post("/render/send", send),
        web.post("/render/claim", claim),
        web.get("/render/job/{job_id}/replay", job_replay),
        web.get("/render/job/{job_id}/skin", job_skin),
        web.post("/render/job/{job_id}/heartbeat", job_heartbeat),
        web.post("/render/job/{job_id}/result", job_result),
        web.post("/render/job/{job_id}/give-back", job_give_back),
        web.get("/render/farm", farm),
    ]

def install(app: web.Application) -> bool:
    if not RENDER_WORKER_TOKEN:
        logger.info("no RENDER_WORKER_TOKEN: renders stay on this host")
        return False
    app.add_routes(make_routes())
    logger.info("render worker endpoints ready")
    return True
