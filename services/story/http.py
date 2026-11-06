import json
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from services.story import service
from services.story.engine import StoryError

STATUS = {
    "STEP_MOVED": 409,
    "LOST_STEP": 409,
    "NOT_A_SCENE": 409,
    "NOT_A_CHOICE": 409,
    "NOT_A_TASK": 409,
    "NOT_AN_END": 409,
    "NO_MAP": 409,
    "NO_SUCH_OPTION": 422,
    "NOT_LINKED": 403,
    "TOO_SOON": 429,
}
LANGS = ("ru", "en")


def _lang(request: web.Request) -> str:
    said = request.query.get("lang", "ru")
    return said if said in LANGS else "ru"


async def _body(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    try:
        said = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(text="not json")
    if not isinstance(said, dict):
        raise web.HTTPBadRequest(text="not an object")
    return said


def _step(said: dict[str, Any]) -> Optional[str]:
    step = said.get("step")
    if step is not None and not isinstance(step, str):
        raise web.HTTPBadRequest(text="step must be a name")
    return step


def routes(guard, who, factory_of: Callable[[], Any], osu_of: Callable[[], Any] = lambda: None) -> list[web.RouteDef]:
    def handler(work: Callable[[web.Request, int, dict[str, Any]], Awaitable[Any]]):
        async def handle(request: web.Request) -> web.Response:
            denied = await guard(request)
            if denied is not None:
                return denied
            owner = await who(request)
            if owner is None:
                return web.json_response({"error": "no one"}, status=404)
            if owner.player_id is None:
                return web.json_response({"error": "NOT_LINKED"}, status=403)
            said = await _body(request) if request.method != "GET" else {}
            try:
                return web.json_response(await work(request, owner.player_id, said))
            except StoryError as error:
                return web.json_response({"error": error.code}, status=STATUS.get(error.code, 400))
        return handle

    async def state(request, player, said):
        return await service.state(factory_of(), player, _lang(request))

    async def next_step(request, player, said):
        return await service.next_step(factory_of(), player, _step(said), _lang(request))

    async def choose(request, player, said):
        option = said.get("option")
        if not isinstance(option, str):
            raise web.HTTPBadRequest(text="option must be a name")
        return await service.pick_option(factory_of(), player, _step(said), option, _lang(request))

    async def check(request, player, said):
        osu = osu_of()
        if osu is None:
            return {"error": "no osu"}
        return await service.check(factory_of(), osu, player, _step(said), _lang(request))

    async def finish(request, player, said):
        return await service.finish_chapter(factory_of(), player, _step(said), _lang(request))

    return [
        web.get("/render/story", handler(state)),
        web.post("/render/story/next", handler(next_step)),
        web.post("/render/story/choose", handler(choose)),
        web.post("/render/story/check", handler(check)),
        web.post("/render/story/finish", handler(finish)),
    ]
