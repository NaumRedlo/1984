import re
from time import monotonic
from typing import Optional

import aiohttp
from aiohttp import web

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
MOST = 2 * 1024 * 1024
KEPT_FOR = 300.0
KEPT_MOST = 64
PATIENCE = aiohttp.ClientTimeout(total=12)

_kept: dict[str, tuple[float, str]] = {}


def fits(name: str) -> bool:
    return bool(NAME.match(name))


def held(name: str, *, now: Optional[float] = None) -> Optional[str]:
    now = monotonic() if now is None else now
    found = _kept.get(name.lower())
    if found is None or now - found[0] >= KEPT_FOR:
        return None
    return found[1]


def keep(name: str, page: str, *, now: Optional[float] = None) -> None:
    now = monotonic() if now is None else now
    for key in [key for key, (at, _) in _kept.items() if now - at >= KEPT_FOR]:
        _kept.pop(key, None)
    while len(_kept) >= KEPT_MOST:
        _kept.pop(min(_kept, key=lambda key: _kept[key][0]), None)
    _kept[name.lower()] = (now, page)


async def read(name: str) -> Optional[str]:
    async with aiohttp.ClientSession(timeout=PATIENCE) as session:
        async with session.get(f"https://t.me/s/{name}", headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=False) as reply:
            if reply.status != 200:
                return None
            body = await reply.content.read(MOST + 1)
            if len(body) > MOST:
                return None
            return body.decode("utf-8", errors="replace")


def routes(guard, reader=read) -> list[web.RouteDef]:
    async def page(request: web.Request) -> web.Response:
        denied = await guard(request)
        if denied is not None:
            return denied
        name = request.match_info["name"]
        if not fits(name):
            raise web.HTTPBadRequest(text="not a channel name")
        found = held(name)
        if found is None:
            try:
                found = await reader(name)
            except (aiohttp.ClientError, TimeoutError, OSError):
                found = None
            if found is None:
                return web.json_response({"error": "channel unavailable"}, status=502)
            keep(name, found)
        return web.Response(text=found, content_type="text/html")

    return [web.get("/render/news/channel/{name}", page)]
