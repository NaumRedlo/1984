import asyncio
import hashlib
import json
import math
import os
import secrets
import tempfile
from typing import Optional

from aiogram import Bot, types
from aiohttp import web

from config.settings import APP_VIDEO_MOST, RENDER_REPLAY_MOST, RENDER_WORKER_TOKEN, SHARED_REPLAYS_DIR, SHARED_REPLAYS_STORAGE_MOST, TRUSTED_PROXY_HOPS
from services.render_farm import app_storage, community as gathered, donated, invites, members, pairing, players, publications, replays, skins, videos
from services.render_farm.queue import STANDARD, RenderQueue, queue as default_queue
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

async def _scopes(owner: invites.Owner) -> Optional[str]:
    from sqlalchemy import or_, select

    from db.database import AsyncSessionFactory
    from db.models.oauth_token import OAuthToken

    mine = []
    if owner.telegram_id:
        mine.append(OAuthToken.telegram_id == owner.telegram_id)
    if owner.player_id is not None:
        mine.append(OAuthToken.player_id == owner.player_id)
    if not mine:
        return None
    async with AsyncSessionFactory() as session:
        row = (await session.execute(select(OAuthToken.scopes).where(or_(*mine)))).first()
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

FETCH_PATIENCE = 900
THUMB_KEEP = 3600.0
_thumb_cache = TTLCache(maxsize=200, ttl=THUMB_KEEP)

CHAT_KEEP = 600.0
_chat_cache = TTLCache(maxsize=500, ttl=CHAT_KEEP)

def _etag_of(body: dict) -> str:
    steady = {key: value for key, value in body.items() if key != "at"}
    return '"' + hashlib.sha1(json.dumps(steady, sort_keys=True).encode()).hexdigest()[:24] + '"'

def forget_memories() -> None:
    _chat_cache.clear()
    members.forget()
    gathered.forget_holders()
    gathered.forget_gathered()
_fetching: dict[str, asyncio.Lock] = {}
_reading: dict[str, int] = {}

def _local(path: str) -> bool:
    return bool(path) and os.path.isabs(path) and os.path.isfile(path)

async def _fetched(bot: Bot, file_id: str, key: str):
    lock = _fetching.setdefault(key, asyncio.Lock())
    async with lock:
        file = await bot.get_file(file_id, request_timeout=FETCH_PATIENCE)
        path = file.file_path or ""
        if not _local(path):
            return file, None
        handle = open(path, "rb")
        _reading[path] = _reading.get(path, 0) + 1
        return file, handle

async def _released(key: str, path: str, handle) -> None:
    async with _fetching.setdefault(key, asyncio.Lock()):
        handle.close()
        left = _reading.get(path, 1) - 1
        if left > 0:
            _reading[path] = left
            return
        _reading.pop(path, None)
        try:
            os.unlink(path)
        except OSError:
            pass

async def _small(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buffer = await bot.download_file(file.file_path)
    return buffer.read() if hasattr(buffer, "read") else bytes(buffer)

async def _who(request: web.Request) -> Optional[invites.Owner]:
    base = invites.owner(_token(request))
    if base is None or base.player_id is None:
        return base
    from db.database import AsyncSessionFactory
    from db.models.player import Player

    async with AsyncSessionFactory() as session:
        player = await session.get(Player, base.player_id)
    if player is None:
        return None
    return invites.Owner(int(player.telegram_id or 0), player.osu_username or base.name, player.id)

def _osu_sign_in() -> bool:
    from config.settings import OSU_CLIENT_ID, OSU_CLIENT_SECRET

    return bool(OSU_CLIENT_ID and OSU_CLIENT_SECRET)

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
        invites.touch(_token(request))
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
        handed["most"] = APP_VIDEO_MOST if job.video_id is not None or job.in_app else _max_send_bytes()
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
        job = line.get(job_id)
        maximum = APP_VIDEO_MOST if job is not None and (job.video_id is not None or job.in_app) else _max_send_bytes()
        try:
            path, written = await _spool(request, maximum, "render-result-")
        except ValueError:
            line.give_back(job_id, key, "the video was too large to send")
            who.handed_back(key)
            return web.json_response({"error": "too large", "most": maximum}, status=413)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        if job is not None and (job.video_id is not None or job.in_app):
            with open(path, "rb") as source:
                header = source.read(12)
            if written < 12 or header[4:8] != b"ftyp":
                os.unlink(path)
                line.give_back(job_id, key, "worker returned an invalid video")
                return web.json_response({"error": "not mp4"}, status=400)
            from db.database import AsyncSessionFactory
            from db.models.player import Player
            from db.models.shared_video import SharedVideo
            from sqlalchemy import select

            try:
                async with AsyncSessionFactory() as session:
                    await app_storage.purge(session)
                    if job.video_id is not None:
                        video = await session.get(SharedVideo, job.video_id)
                    else:
                        player = (await session.execute(select(Player).where(Player.telegram_id == job.requester))).scalar_one_or_none()
                        if player is None or not os.path.isfile(job.replay_path):
                            os.unlink(path)
                            line.give_back(job_id, key, "render source unavailable")
                            return web.json_response({"error": "render source unavailable"}, status=410)
                        with open(job.replay_path, "rb") as source:
                            replay_data = source.read()
                        replay_key = await app_storage.keep_replay(replay_data, SHARED_REPLAYS_DIR, SHARED_REPLAYS_STORAGE_MOST)
                        if replay_key is None:
                            os.unlink(path)
                            line.give_back(job_id, key, "replay storage is full")
                            return web.json_response({"error": "replay storage is full"}, status=507)
                        owner = invites.Owner(job.requester, player.osu_username, player.id)
                        meta = dict(job.video_meta or {})
                        meta.update(_meta(request))
                        meta["map_hash"] = job.beatmap_md5
                        meta["settings"] = job.settings or STANDARD
                        video = await videos.keep_app(session, owner, meta, written, job.skin)
                        video.replay_sha256 = replay_key
                        from db.models.shared_video import VideoDelivery

                        session.add(VideoDelivery(video_id=video.id, sender_id=player.id, recipient_id=player.id))
                    if video is None or video.kind != "app" or not await app_storage.store(session, video, path, written):
                        os.unlink(path)
                        if job.in_app and video is not None:
                            await session.rollback()
                            await session.delete(video)
                            await session.commit()
                        line.give_back(job_id, key, "video storage is full")
                        return web.json_response({"error": "video storage is full"}, status=507)
            except Exception as exc:
                if os.path.exists(path):
                    os.unlink(path)
                logger.warning("app video result could not be stored: %s", exc)
                return web.json_response({"error": "video storage failed"}, status=500)
            if not line.finish(job_id, key, {"bytes": written, "video": video.id}):
                return web.json_response({"error": "not yours"}, status=409)
            who.delivered(key)
            return web.json_response({"ok": True})
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
            "osu": _osu_sign_in(),
        })

    async def pair_osu(request: web.Request) -> web.Response:
        if not _osu_sign_in():
            return web.json_response({"error": "no osu! sign-in"}, status=404)
        state = pairing.osu_state(request.match_info["code"])
        if state is None:
            return web.json_response({"error": "gone"}, status=404)
        from services.oauth.server import authorize_url

        raise web.HTTPFound(authorize_url(state))

    async def link_telegram(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None or owner.player_id is None:
            return web.json_response({"error": "no one"}, status=404)
        if owner.telegram_id:
            return web.json_response({"error": "linked"}, status=409)
        code = pairing.ask_telegram(owner.player_id, owner.name, _address(request))
        if code is None:
            return web.json_response({"error": "too many"}, status=429)
        return web.json_response({"code": invites.pretty(code), "link": pairing.link(code), "expires_in": int(pairing.GOOD_FOR)})

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
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        body = {"telegram_id": owner.telegram_id, "name": owner.name, "username": "", "avatar": False,
                "telegram": bool(owner.telegram_id), "player": owner.player_id}
        if _bot is not None and owner.telegram_id:
            try:
                chat = await _bot.get_chat(owner.telegram_id)
                body["username"] = chat.username or ""
                body["name"] = " ".join(p for p in (chat.first_name, chat.last_name) if p) or owner.name
                body["avatar"] = chat.photo is not None
            except Exception as exc:
                logger.warning("cannot describe %s: %s", owner.telegram_id, exc)
        return web.json_response(body)

    async def presence(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        token = _token(request)
        if invites.owner(token) is None:
            return web.json_response({"error": "no one"}, status=404)
        invites.touch(token, keep=invites.PRESENCE_BEAT_FOR)
        return web.Response(status=204)

    async def me_avatar(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None or _bot is None or not owner.telegram_id:
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
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        if not owner.telegram_id:
            return web.json_response([])
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
                    title, photo = await _chat_card(chat_id)
                    if not await _member(chat_id, owner.telegram_id):
                        continue
                except Exception as exc:
                    logger.info("chat %s is not reachable: %s", chat_id, exc)
                    continue
            rows.append({"id": chat_id, "title": title, "private": chat_id > 0, "photo": photo})
        return web.json_response(rows)

    async def _member(chat_id: int, telegram_id: int) -> bool:
        return await members.is_member(_bot, chat_id, telegram_id)

    async def _chat_card(chat_id: int) -> tuple[str, bool]:
        kept = _chat_cache.get(chat_id)
        if kept is not None:
            return kept
        chat = await _bot.get_chat(chat_id)
        card = (chat.title or chat.full_name or "", chat.photo is not None)
        _chat_cache[chat_id] = card
        return card

    async def chat_avatar(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
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

    async def _pinned(telegram_id: int) -> Optional[int]:
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            return await players.pinned_chat(session, telegram_id)

    async def _group_for(telegram_id: int, said: str) -> tuple[Optional[int], bool]:
        chat_id: Optional[int] = int(said) if said.lstrip("-").isdigit() else None
        if chat_id is None or chat_id > 0:
            pinned = await _pinned(telegram_id)
            if pinned is not None and await _member(pinned, telegram_id):
                return pinned, True
            for group in await _groups_of(telegram_id):
                if await _member(group, telegram_id):
                    return group, True
            return None, True
        return chat_id, await _member(chat_id, telegram_id)

    async def community(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        chat_id, allowed = await _group_for(owner.telegram_id, request.query.get("chat", ""))
        if not allowed:
            return web.json_response({"error": "not your chat"}, status=403)

        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await gathered.gather_shared(session, chat_id, owner.telegram_id) if chat_id is not None else {
                "chat": None, "week": 0, "people": [], "live": [], "happened": [], "titles": gathered.titles_catalogue(), "at": None,
            }
            body["me"] = await gathered.own(session, owner.telegram_id, chat_id)
            if body["me"] is None and owner.player_id is not None:
                body["me"] = await players.one(session, owner.player_id, owner.telegram_id, me=owner.player_id)
        body["group"] = ""
        body["photo"] = False
        if chat_id is not None and _bot is not None:
            try:
                body["group"], body["photo"] = await _chat_card(chat_id)
            except Exception as exc:
                logger.info("chat %s is not reachable: %s", chat_id, exc)
        text = json.dumps(body)
        tag = _etag_of(body)
        if request.headers.get("If-None-Match") == tag:
            return web.Response(status=304, headers={"ETag": tag})
        reply = web.Response(text=text, content_type="application/json", headers={"ETag": tag})
        reply.enable_compression()
        return reply

    async def map_board(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        said = request.match_info["beatmap"]
        if not said.isdigit():
            return web.json_response({"error": "no map"}, status=400)
        chat_id, allowed = await _group_for(owner.telegram_id, request.query.get("chat", ""))
        if not allowed:
            return web.json_response({"error": "not your chat"}, status=403)
        if chat_id is None:
            return web.json_response({"error": "no chat"}, status=404)

        from db.database import AsyncSessionFactory
        from services.render_farm import maps

        async with AsyncSessionFactory() as session:
            body = await maps.board(session, _osu, int(said), chat_id, owner.telegram_id, sync=request.query.get("sync", "1") != "0")
        return web.json_response(body)

    async def someone(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
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
        owner = await _who(request)
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
                kept = await gathered.keep_card(session, owner.telegram_id, card, player_id=owner.player_id)
            except ValueError:
                return web.json_response({"error": "too large"}, status=413)
        return web.json_response({"kept": kept})

    async def card(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
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
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        from db.database import AsyncSessionFactory
        from tasks import live_tracker

        async with AsyncSessionFactory() as session:
            user = await gathered.chosen(session, owner.telegram_id)
            if user is None and owner.player_id is not None:
                user = await videos.player_of(session, owner)
        if user is None or not user.osu_user_id:
            return web.json_response({"error": "not registered"}, status=404)
        heard = live_tracker.nudge(int(user.osu_user_id))
        return web.json_response({"heard": heard}, status=202)

    async def witnessed_play(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        told = await _json_object(request)
        from db.database import AsyncSessionFactory
        from services.render_farm import witnessed
        from tasks import live_tracker

        async with AsyncSessionFactory() as session:
            player = await videos.player_of(session, owner)
            if player is None:
                return web.json_response({"error": "not registered"}, status=404)
            verdict, row = await witnessed.keep(session, player.id, told)
            osu_user_id = player.osu_user_id
        if verdict == witnessed.BAD:
            return web.json_response({"error": "not a play"}, status=400)
        if verdict in (witnessed.TOO_SOON, witnessed.TOO_MANY):
            return web.json_response({"error": verdict}, status=429)
        if verdict == witnessed.KEPT:
            await witnessed.learn_soon(AsyncSessionFactory, _osu, row.id)
            if osu_user_id and row.passed:
                live_tracker.nudge(int(osu_user_id))
        return web.json_response({"kept": verdict == witnessed.KEPT, "play": row.id if row is not None else None}, status=201 if verdict == witnessed.KEPT else 200)

    async def witness_session(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        said = await _json_object(request)
        from db.database import AsyncSessionFactory
        from services.render_farm import witness_sessions

        async with AsyncSessionFactory() as session:
            player = await videos.player_of(session, owner)
            if player is None:
                return web.json_response({"error": "not registered"}, status=404)
            verdict = await witness_sessions.keep(session, player, said)
            await session.commit()
        if verdict == witness_sessions.BAD:
            return web.json_response({"error": "not a session"}, status=400)
        return web.json_response({"kept": True}, status=200)

    async def local_history(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        said = await _json_object(request)
        from db.database import AsyncSessionFactory
        from services.render_farm import local_scores

        async with AsyncSessionFactory() as session:
            player = await videos.player_of(session, owner)
            if player is None:
                return web.json_response({"error": "not registered"}, status=404)
            verdict, kept = await local_scores.keep(session, player.id, said)
        if verdict == local_scores.BAD:
            return web.json_response({"error": "not scores"}, status=400)
        if verdict == local_scores.TOO_MANY:
            return web.json_response({"error": verdict}, status=429)
        return web.json_response({"kept": kept}, status=200)

    async def wear_title(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
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
            verdict = await gathered.wear(session, owner.telegram_id, chat_id, code, player_id=owner.player_id)
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
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        friends_key = owner.telegram_id or ("player", owner.player_id)
        kept = _friends_cache.get(friends_key)
        if kept is not None:
            return web.json_response({"friends": kept})
        scopes = await _scopes(owner)
        if scopes is None:
            return web.json_response({"need": "link"}, status=409)
        if "friends.read" not in scopes.split():
            return web.json_response({"need": "friends"}, status=409)
        from services.oauth.token_manager import token_of

        token = await token_of(owner)
        if not token:
            return web.json_response({"need": "link"}, status=409)
        raw = await _osu_friends(token)
        if raw is None:
            return web.json_response({"error": "osu! did not answer"}, status=502)
        listed = gathered.friends_from(raw)
        _friends_cache[friends_key] = listed
        return web.json_response({"friends": listed})

    async def every_player(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await players.everyone(session, owner.telegram_id, query=request.query.get("q", ""),
                                          signed=invites.linked_players(), me=owner.player_id)
        return web.json_response(body)

    async def one_player(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        said = request.match_info.get("player_id", "")
        if not said.isdigit():
            return web.json_response({"error": "no one"}, status=400)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await players.one(session, int(said), owner.telegram_id, signed=invites.linked_players(), me=owner.player_id)
        if body is None:
            return web.json_response({"error": "no one"}, status=404)
        return web.json_response(body)

    async def pin_read(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await players.pin_of(session, owner)
        return web.json_response(body)

    async def pin_write(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        said = str((await _json_object(request)).get("chat") or "")
        if not said.lstrip("-").isdigit() or int(said) >= 0:
            return web.json_response({"error": "no chat"}, status=400)
        chat_id = int(said)
        if not await _member(chat_id, owner.telegram_id):
            return web.json_response({"error": "not your chat"}, status=403)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            verdict, body = await players.pin(session, owner, chat_id)
        status = {players.PINNED: 200, players.TOO_SOON: 409, players.NOT_REGISTERED: 404, players.NOT_THERE: 403}[verdict]
        if verdict != players.PINNED:
            body = {**body, "error": verdict}
        return web.json_response(body, status=status)

    async def donate_replay(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        try:
            path, _ = await _spool(request, RENDER_REPLAY_MOST, "donated-replay-")
        except ValueError:
            return web.json_response({"error": "too large", "most": RENDER_REPLAY_MOST}, status=413)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        try:
            with open(path, "rb") as got:
                data = got.read()
        finally:
            os.unlink(path)
        if not donated.looks_like_replay(data):
            return web.json_response({"error": "not a replay"}, status=400)
        try:
            known = donated.keep(data, owner.telegram_id)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        if known is None:
            return web.json_response({"error": "full"}, status=507)
        return web.json_response({"ok": True, "known": known})

    async def send(request: web.Request) -> web.Response:
        bad = await guard(request)
        if bad:
            return bad
        owner = await _who(request)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        if _bot is None:
            return web.json_response({"error": "bot asleep"}, status=503)
        if not owner.telegram_id:
            return web.json_response({"error": "no telegram"}, status=409)
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
        body = {"ok": True, "message_id": sent.message_id}
        kind, media = videos.media_of(sent)
        if media is not None:
            from db.database import AsyncSessionFactory

            try:
                async with AsyncSessionFactory() as session:
                    body["video"] = await videos.keep(session, owner.telegram_id, kind, media, meta)
            except Exception as exc:
                logger.warning("the video sent to %s was not remembered: %s", where, exc)
        return web.json_response(body)

    async def app_video(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        if owner.player_id is None:
            return web.json_response({"error": "not registered"}, status=403)
        meta = _meta(request)
        if not videos.described(meta)["map_hash"]:
            return web.json_response({"error": "no map hash"}, status=400)
        try:
            path, written = await _spool(request, APP_VIDEO_MOST, "app-video-")
        except ValueError:
            return web.json_response({"error": "too large", "most": APP_VIDEO_MOST}, status=413)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        if written < 12:
            os.unlink(path)
            return web.json_response({"error": "empty video"}, status=400)
        with open(path, "rb") as source:
            header = source.read(12)
        if header[4:8] != b"ftyp":
            os.unlink(path)
            return web.json_response({"error": "not mp4"}, status=400)
        from db.database import AsyncSessionFactory
        from db.models.shared_video import SharedVideo

        try:
            skin = await skins.chosen_for(owner.telegram_id) if owner.telegram_id else None
            async with AsyncSessionFactory() as session:
                await app_storage.purge(session)
                video = await videos.keep_app(session, owner, meta, written, skin)
                if not await app_storage.store(session, video, path, written):
                    await session.delete(video)
                    await session.commit()
                    os.unlink(path)
                    return web.json_response({"error": "video storage is full"}, status=507)
        except Exception as exc:
            if os.path.exists(path):
                os.unlink(path)
            logger.warning("app video upload failed: %s", exc)
            return web.json_response({"error": "video storage failed"}, status=500)
        return web.json_response({"ok": True, "video": video.id})

    async def asking(request: web.Request):
        bad = await guard(request)
        if bad:
            return None, bad
        owner = await _who(request)
        if owner is None:
            return None, web.json_response({"error": "no one"}, status=404)
        return owner, None

    def numbered(request: web.Request, name: str) -> Optional[int]:
        said = request.match_info.get(name, "")
        return int(said) if said.isdigit() else None

    async def video_receivers(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            people = await videos.receivers(session, owner, invites.linked(), invites.linked_players())
        if people is None:
            return web.json_response({"error": "not registered"}, status=404)
        return web.json_response({"people": people})

    async def app_video_state(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        video_id = numbered(request, "video")
        if video_id is None:
            return web.json_response({"error": "no video"}, status=400)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            video = await videos.owned(session, owner, video_id)
            if video is None or video.kind != "app" or not app_storage.available(video):
                return web.json_response({"error": "video unavailable"}, status=404)
            return web.json_response({"ready": True})

    async def video_replay(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        video_id = numbered(request, "video")
        if video_id is None:
            return web.json_response({"error": "no video"}, status=400)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            video = await videos.owned(session, owner, video_id)
            if video is None:
                return web.json_response({"error": "no video"}, status=404)
        try:
            path, _ = await _spool(request, RENDER_REPLAY_MOST, "shared-replay-")
        except ValueError:
            return web.json_response({"error": "too large", "most": RENDER_REPLAY_MOST}, status=413)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        try:
            with open(path, "rb") as got:
                data = got.read()
        finally:
            os.unlink(path)
        if not donated.looks_like_replay(data):
            return web.json_response({"error": "not a replay"}, status=400)
        try:
            if video.kind == "app":
                known = await app_storage.keep_replay(data, SHARED_REPLAYS_DIR, SHARED_REPLAYS_STORAGE_MOST)
            else:
                known = donated.keep(data, owner.telegram_id, folder=SHARED_REPLAYS_DIR, most=SHARED_REPLAYS_STORAGE_MOST)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        if known is None:
            return web.json_response({"error": "full"}, status=507)
        async with AsyncSessionFactory() as session:
            await videos.attach_replay(session, owner, video_id, hashlib.md5(data).hexdigest(), known if video.kind == "app" else None)
        return web.json_response({"ok": True})

    async def video_share(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        video_id = numbered(request, "video")
        to = (await _json_object(request)).get("to")
        if video_id is None or not isinstance(to, list):
            return web.json_response({"error": "bad request"}, status=400)
        chosen = [item for item in to if isinstance(item, int) and not isinstance(item, bool)]
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            video = await videos.owned(session, owner, video_id)
            if video is not None and video.kind == "app":
                replay_path = app_storage.replay_path(video, SHARED_REPLAYS_DIR)
                if replay_path is None or not replay_path.is_file():
                    return web.json_response({"error": "replay required"}, status=409)
                if not app_storage.available(video):
                    return web.json_response({"error": "video expired"}, status=410)
            body = await videos.share(session, owner, video_id, chosen, invites.linked(), invites.linked_players())
        if body is None:
            return web.json_response({"error": "no video"}, status=404)
        return web.json_response(body)

    async def inbox(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            return web.json_response(await videos.inbox(session, owner))

    async def inbox_accept(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        said = (await _json_object(request)).get("from")
        if said not in videos.ACCEPTS:
            return web.json_response({"error": "bad request"}, status=400)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            kept = await videos.accept(session, owner, said)
        if kept is None:
            return web.json_response({"error": "not registered"}, status=404)
        return web.json_response({"accept": kept})

    async def taken(request: web.Request):
        owner, bad = await asking(request)
        if bad:
            return None, bad
        delivery_id = numbered(request, "delivery")
        if delivery_id is None:
            return None, web.json_response({"error": "no video"}, status=400)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            found = await videos.received(session, owner, delivery_id)
        if found is None:
            return None, web.json_response({"error": "no video"}, status=404)
        return (owner, *found), None

    async def inbox_restore_state(request: web.Request) -> web.Response:
        found, bad = await taken(request)
        if bad:
            return bad
        _, _, video, _ = found
        if video.kind != "app":
            return web.json_response({"state": "ready"})
        if app_storage.available(video):
            return web.json_response({"state": "ready"})
        job = line.for_video(video.id)
        return web.json_response({"state": "rendering" if job else "expired"})

    async def inbox_restore(request: web.Request) -> web.Response:
        found, bad = await taken(request)
        if bad:
            return bad
        owner, _, video, _ = found
        if video.kind != "app":
            return web.json_response({"error": "not an app video"}, status=409)
        if app_storage.available(video):
            return web.json_response({"state": "ready"})
        if line.for_video(video.id):
            return web.json_response({"state": "rendering"})
        replay_path = app_storage.replay_path(video, SHARED_REPLAYS_DIR)
        if replay_path is None or not replay_path.is_file() or not video.map_hash:
            return web.json_response({"error": "render source unavailable"}, status=410)
        settings = dict(STANDARD)
        try:
            look = json.loads(video.settings) if video.settings else {}
        except (ValueError, TypeError):
            look = {}
        if isinstance(look, dict):
            for name, lower, upper in (("width", 640, 3840), ("height", 360, 2160), ("fps", 24, 240)):
                value = look.get(name)
                if type(value) is int:
                    settings[name] = max(lower, min(upper, value))
            for name in ("music", "hitsounds"):
                value = look.get(name)
                if type(value) in (int, float) and math.isfinite(value):
                    settings[name] = max(0.0, min(1.0, float(value)))
            if isinstance(look.get("play"), dict):
                play = {}
                for name in ("hud", "cursor_grows", "map_sounds", "skin_sounds", "snaking", "hit_lighting",
                             "cursor_trail", "key_overlay", "error_meter", "unstable_rate", "show_300",
                             "storyboard", "map_video"):
                    if type(look["play"].get(name)) is bool:
                        play[name] = look["play"][name]
                for name, maximum in (("dim", 100), ("blur", 100), ("cursor_size", 1000), ("meter_size", 1000)):
                    value = look["play"].get(name)
                    if type(value) is int:
                        play[name] = max(0, min(maximum, value))
                settings["play"] = play
        skin = skins.described(video.skin_name, video.owner) if video.skin_name else None
        if skin is not None and skin.get("hash") != video.skin_hash:
            skin = None
        line.offer(str(replay_path), videos.caption(video, None) or "Shared video", beatmap_md5=video.map_hash,
                   skin=skin, requester=video.owner, video_id=video.id, settings=settings)
        return web.json_response({"state": "rendering"}, status=202)

    async def inbox_thumb(request: web.Request) -> web.Response:
        found, bad = await taken(request)
        if bad:
            return bad
        _, _, video, _ = found
        if not video.thumb_id or _bot is None:
            return web.json_response({"error": "no picture"}, status=404)
        data = _thumb_cache.get(video.id)
        if data is None:
            try:
                data = await _small(_bot, video.thumb_id)
            except Exception as exc:
                logger.info("no picture for video %s: %s", video.id, exc)
                return web.json_response({"error": "no picture"}, status=404)
            _thumb_cache[video.id] = data
        return web.Response(body=data, content_type="image/jpeg")

    async def inbox_video(request: web.Request) -> web.StreamResponse:
        found, bad = await taken(request)
        if bad:
            return bad
        _, _, video, _ = found
        if video.kind == "app":
            if not app_storage.available(video):
                return web.json_response({"error": "video expired"}, status=410)
            return web.FileResponse(app_storage.path_for(video.id), headers={"Content-Type": "video/mp4", "X-Content-SHA256": video.storage_hash or ""})
        if _bot is None:
            return web.json_response({"error": "bot asleep"}, status=503)
        key = video.file_unique_id or video.file_id
        try:
            file, handle = await _fetched(_bot, video.file_id, key)
        except Exception as exc:
            logger.warning("telegram did not give video %s: %s", video.id, exc)
            return web.json_response({"error": "telegram did not answer"}, status=502)
        path = file.file_path or ""
        size = os.fstat(handle.fileno()).st_size if handle is not None else (file.file_size or video.size or 0)
        reply = web.StreamResponse(headers={"Content-Type": "video/mp4"})
        if size:
            reply.content_length = int(size)
        try:
            await reply.prepare(request)
            if handle is not None:
                while True:
                    chunk = await asyncio.to_thread(handle.read, _CHUNK)
                    if not chunk:
                        break
                    await reply.write(chunk)
            else:
                url = _bot.session.api.file_url(_bot.token, path)
                async for chunk in _bot.session.stream_content(url, timeout=FETCH_PATIENCE, chunk_size=_CHUNK):
                    await reply.write(chunk)
            await reply.write_eof()
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("video %s stopped half way: %s", video.id, exc)
        finally:
            if handle is not None:
                await asyncio.shield(_released(key, path, handle))
        return reply

    async def inbox_replay(request: web.Request) -> web.StreamResponse:
        found, bad = await taken(request)
        if bad:
            return bad
        _, _, video, _ = found
        path = app_storage.replay_path(video, SHARED_REPLAYS_DIR)
        if path is None or not path.is_file():
            return web.json_response({"error": "no replay"}, status=404)
        return web.FileResponse(path)

    async def inbox_telegram(request: web.Request) -> web.Response:
        found, bad = await taken(request)
        if bad:
            return bad
        owner, _, video, sender = found
        if video.kind == "app":
            return web.json_response({"error": "only in app"}, status=404)
        if _bot is None:
            return web.json_response({"error": "bot asleep"}, status=503)
        if not owner.telegram_id:
            return web.json_response({"error": "no telegram"}, status=409)
        send_as = {"animation": _bot.send_animation, "document": _bot.send_document}.get(video.kind or "", _bot.send_video)
        try:
            sent = await send_as(owner.telegram_id, video.file_id, caption=videos.caption(video, sender) or None)
        except Exception as exc:
            logger.warning("passing video %s to %s failed: %s", video.id, owner.telegram_id, exc)
            return web.json_response({"error": str(exc)}, status=502)
        return web.json_response({"ok": True, "message_id": sent.message_id})

    async def inbox_seen(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        delivery_id = numbered(request, "delivery")
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            done = delivery_id is not None and await videos.seen(session, owner, delivery_id)
        return web.json_response({"ok": done}, status=200 if done else 404)

    async def inbox_drop(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        delivery_id = numbered(request, "delivery")
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            done = delivery_id is not None and await videos.drop(session, owner, delivery_id)
        return web.json_response({"ok": done}, status=200 if done else 404)

    async def replays_state(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await replays.state(session, owner)
        if body is None:
            return web.json_response({"error": "not registered"}, status=404)
        return web.json_response(body)

    async def replays_switch(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        on = (await _json_object(request)).get("on")
        if not isinstance(on, bool):
            return web.json_response({"error": "bad request"}, status=400)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await replays.switch(session, owner, on)
        if body is None:
            return web.json_response({"error": "not registered"}, status=404)
        return web.json_response(body)

    async def replay_share(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        try:
            path, _ = await _spool(request, RENDER_REPLAY_MOST, "player-replay-")
        except ValueError:
            return web.json_response({"error": "too large", "most": RENDER_REPLAY_MOST}, status=413)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        try:
            with open(path, "rb") as got:
                data = got.read()
        finally:
            os.unlink(path)
        from db.database import AsyncSessionFactory

        try:
            async with AsyncSessionFactory() as session:
                verdict = await replays.keep(session, owner, data, _meta(request), osu=_osu)
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        status = {
            replays.KEPT: 200, replays.KNOWN: 200, replays.NOT_REGISTERED: 404, replays.NOT_SHARING: 409,
            replays.NOT_A_REPLAY: 400, replays.NOT_YOURS: 403, replays.FULL: 507,
        }[verdict]
        if status != 200:
            return web.json_response({"error": verdict}, status=status)
        return web.json_response({"ok": True, "known": verdict == replays.KNOWN})

    async def replays_listed(request: web.Request) -> web.Response:
        owner, bad = await asking(request)
        if bad:
            return bad
        scope = replays.ALL if request.query.get("scope") == replays.ALL else replays.CHAT
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            body = await replays.listed(session, owner, scope)
        if body is None:
            return web.json_response({"error": "not registered"}, status=404)
        return web.json_response(body)

    async def replay_file(request: web.Request) -> web.StreamResponse:
        owner, bad = await asking(request)
        if bad:
            return bad
        said = request.match_info.get("hash", "").lower()
        if len(said) != 32 or not all(c in "0123456789abcdef" for c in said):
            return web.json_response({"error": "no replay"}, status=400)
        from db.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            allowed = await replays.readable(session, owner, said)
        path = replays.path_of(said)
        if not allowed or not os.path.isfile(path):
            return web.json_response({"error": "no replay"}, status=404)
        return web.FileResponse(path)

    return [
        web.post("/render/pair", pair),
        web.get("/render/pair/{code}", pair_status),
        web.get("/render/pair/{code}/osu", pair_osu),
        web.post("/render/me/telegram", link_telegram),
        web.get("/render/hello", hello),
        web.get("/render/me", me),
        web.get("/render/me/avatar", me_avatar),
        web.get("/render/me/chats", chats),
        web.get("/render/chat/{chat_id}/avatar", chat_avatar),
        web.get("/render/community", community),
        web.get("/render/community/person", someone),
        web.get("/render/maps/{beatmap}/board", map_board),
        web.post("/render/me/profile", share_card),
        web.post("/render/me/title", wear_title),
        web.post("/render/me/played", played),
        web.post("/render/me/presence", presence),
        web.post("/render/me/play", witnessed_play),
        web.post("/render/me/session", witness_session),
        web.post("/render/me/history", local_history),
        web.get("/render/me/friends", friends),
        web.get("/render/me/card", card),
        web.post("/render/send", send),
        web.post("/render/videos", app_video),
        web.get("/render/videos/receivers", video_receivers),
        web.get("/render/videos/{video}", app_video_state),
        web.put("/render/videos/{video}/replay", video_replay),
        web.post("/render/videos/{video}/share", video_share),
        web.get("/render/me/inbox", inbox),
        web.post("/render/me/accept", inbox_accept),
        web.get("/render/inbox/{delivery}/thumb", inbox_thumb),
        web.get("/render/inbox/{delivery}/video", inbox_video),
        web.get("/render/inbox/{delivery}/restore", inbox_restore_state),
        web.post("/render/inbox/{delivery}/restore", inbox_restore),
        web.get("/render/inbox/{delivery}/replay", inbox_replay),
        web.post("/render/inbox/{delivery}/telegram", inbox_telegram),
        web.post("/render/inbox/{delivery}/seen", inbox_seen),
        web.delete("/render/inbox/{delivery}", inbox_drop),
        web.get("/render/me/replays", replays_state),
        web.post("/render/me/replays", replays_switch),
        web.post("/render/replays", replay_share),
        web.get("/render/replays", replays_listed),
        web.get("/render/replays/{hash}", replay_file),
        web.post("/render/me/replay", donate_replay),
        web.get("/render/players", every_player),
        web.get("/render/players/{player_id}", one_player),
        web.get("/render/me/pin", pin_read),
        web.post("/render/me/pin", pin_write),
        web.post("/render/claim", claim),
        web.get("/render/job/{job_id}/replay", job_replay),
        web.get("/render/job/{job_id}/skin", job_skin),
        web.post("/render/job/{job_id}/heartbeat", job_heartbeat),
        web.post("/render/job/{job_id}/result", job_result),
        web.post("/render/job/{job_id}/give-back", job_give_back),
        web.get("/render/farm", farm),
    ] + publications.routes(guard, _who)

def install(app: web.Application) -> bool:
    if not RENDER_WORKER_TOKEN:
        logger.info("no RENDER_WORKER_TOKEN: renders stay on this host")
        return False
    app.add_routes(make_routes())
    app.cleanup_ctx.append(_video_cleanup)
    logger.info("render worker endpoints ready")
    return True

async def _video_cleanup(app: web.Application):
    from db.database import AsyncSessionFactory

    async def run():
        while True:
            try:
                async with AsyncSessionFactory() as session:
                    await app_storage.purge(session)
            except Exception as exc:
                logger.warning("app video cleanup failed: %s", exc)
            await asyncio.sleep(900)

    task = asyncio.create_task(run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
