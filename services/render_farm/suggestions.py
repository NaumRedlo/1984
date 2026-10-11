from typing import Any, Callable, Optional

from aiohttp import web

MOST = 40
SPEED = {"DT": 1.4, "HR": 1.08}


def bounds(low: float, high: float, mod: str) -> tuple[float, float]:
    by = SPEED.get(mod.upper(), 1.0)
    return (max(0.0, low / by - 0.15), max(0.0, high / by + 0.15))


def query_for(low: float, high: float, mod: str) -> str:
    least, most = bounds(low, high, mod)
    return f"stars>={least:.2f} stars<={most:.2f}"


def picked(reply: Optional[dict], low: float, high: float, mod: str, most: int = MOST) -> list[dict[str, Any]]:
    least, top = bounds(low, high, mod)
    found: list[dict[str, Any]] = []
    for held in (reply or {}).get("beatmapsets") or []:
        if not isinstance(held, dict):
            continue
        for beatmap in held.get("beatmaps") or []:
            if not isinstance(beatmap, dict) or beatmap.get("mode") != "osu":
                continue
            stars = beatmap.get("difficulty_rating")
            if not isinstance(stars, (int, float)) or not least <= stars <= top:
                continue
            identity = beatmap.get("id")
            if not isinstance(identity, int):
                continue
            found.append({
                "id": identity,
                "set": held.get("id"),
                "artist": str(held.get("artist") or ""),
                "title": str(held.get("title") or ""),
                "version": str(beatmap.get("version") or ""),
                "stars": float(stars),
                "length": int(beatmap.get("total_length") or 0),
                "bpm": float(beatmap.get("bpm") or 0),
                "md5": str(beatmap.get("checksum") or ""),
            })
            if len(found) >= most:
                return found
    return found


def routes(guard, osu: Callable[[], Any]) -> list[web.RouteDef]:
    async def suggested(request: web.Request) -> web.Response:
        denied = await guard(request)
        if denied is not None:
            return denied
        try:
            low = float(request.query.get("low", ""))
            high = float(request.query.get("high", ""))
        except ValueError:
            raise web.HTTPBadRequest(text="low and high are numbers")
        mod = request.query.get("mod", "NM").upper()[:2]
        if not (0.0 <= low <= high <= 15.0) or mod not in {"NM", "HD", "HR", "DT", "FM", "TB"}:
            raise web.HTTPBadRequest(text="range or mod out of bounds")
        client = osu()
        if client is None:
            return web.json_response({"error": "osu api unavailable"}, status=503)
        reply = await client.search_beatmapsets(query=query_for(low, high, mod))
        return web.json_response(picked(reply, low, high, mod))

    return [web.get("/render/pools/suggest", suggested)]
