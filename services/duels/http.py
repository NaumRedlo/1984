import json
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from services.duels import live, notify, series, service, states
from services.duels.errors import DuelError

STATUS = {
    "NOT_FOUND": 404,
    "NOT_PARTICIPANT": 403,
    "NOT_LINKED": 403,
    "INVALID_STATE": 409,
    "VERSION_CONFLICT": 409,
    "ALREADY_BUSY": 409,
    "ALREADY_INVITED": 409,
    "NOT_YOUR_TURN": 409,
    "SLOT_UNAVAILABLE": 409,
    "MAP_REVISION_CHANGED": 409,
    "OPERATION_REUSED": 422,
    "SELF_CHALLENGE": 422,
    "ROOM_CONTROL_REQUIRED": 409,
}

def _answer(body: Any, status: int = 200) -> web.Response:
    return web.json_response(body, status=status)

def _refused(error: DuelError) -> web.Response:
    return _answer(error.body(), STATUS.get(error.code, 400))

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

def _operation(request: web.Request, said: dict[str, Any]) -> str:
    key = request.headers.get("Idempotency-Key") or said.get("operation_id")
    if not isinstance(key, str) or not 8 <= len(key) <= 64:
        raise web.HTTPBadRequest(text="Idempotency-Key is required")
    return key

def _version(said: dict[str, Any]) -> Optional[int]:
    value = said.get("expected_version")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise web.HTTPBadRequest(text="expected_version must be a number")
    return value

def routes(guard, who, factory_of: Callable[[], Any]) -> list[web.RouteDef]:
    def handler(work: Callable[[web.Request, int, dict[str, Any]], Awaitable[Any]], status: int = 200):
        async def handle(request: web.Request) -> web.Response:
            denied = await guard(request)
            if denied is not None:
                return denied
            owner = await who(request)
            if owner is None:
                return _answer({"error": "no one"}, 404)
            if owner.player_id is None:
                return _refused(DuelError("NOT_LINKED"))
            said = await _body(request) if request.method != "GET" else {}
            try:
                return _answer(await work(request, owner.player_id, said), status)
            except DuelError as error:
                return _refused(error)
        return handle

    async def invite(request, player, said):
        opponent = said.get("opponent_id")
        if isinstance(opponent, bool) or not isinstance(opponent, int):
            raise web.HTTPBadRequest(text="opponent_id must be a number")
        return await service.invite(factory_of(), player, opponent, _operation(request, said))

    def on_duel(action):
        async def work(request, player, said):
            return await action(request.match_info["duel_id"], player, said, _operation(request, said), _version(said))
        return work

    async def accept(duel_id, player, said, operation, version):
        return await service.accept(factory_of(), duel_id, player, operation, version)

    async def decline(duel_id, player, said, operation, version):
        return await service.decline(factory_of(), duel_id, player, operation, version)

    async def surrender(duel_id, player, said, operation, version):
        current = await service.read(factory_of(), duel_id, player)
        if current["state"] in ("INVITED",):
            raise DuelError("INVALID_STATE", current)
        if "surrender" in current["actions"] and current["state"] in states.BEFORE_PLAY:
            return await service.surrender(factory_of(), duel_id, player, operation, version)
        return await series.give_up(factory_of(), duel_id, player, operation, version)

    async def pool_ack(duel_id, player, said, operation, version):
        manifest = said.get("manifest_hash")
        if not isinstance(manifest, str):
            raise web.HTTPBadRequest(text="manifest_hash is required")
        return await series.pool_ack(factory_of(), duel_id, player, manifest, operation, version)

    async def draft(duel_id, player, said, operation, version):
        slot = said.get("slot")
        if not isinstance(slot, str):
            raise web.HTTPBadRequest(text="slot is required")
        return await series.draft(factory_of(), duel_id, player, slot, operation, version)

    async def ready(duel_id, player, said, operation, version):
        mods = said.get("tb_mods")
        if mods is not None and (not isinstance(mods, list) or not all(isinstance(mod, str) for mod in mods)):
            raise web.HTTPBadRequest(text="tb_mods must be a list")
        return await series.ready(factory_of(), duel_id, player, operation, tb_mods=mods, expected_version=version)

    async def bonus(duel_id, player, said, operation, version):
        accept = said.get("accept")
        mods = said.get("tb_mods")
        if not isinstance(accept, bool):
            raise web.HTTPBadRequest(text="accept must be true or false")
        if mods is not None and (not isinstance(mods, list) or not all(isinstance(mod, str) for mod in mods)):
            raise web.HTTPBadRequest(text="tb_mods must be a list")
        return await series.bonus(factory_of(), duel_id, player, accept, operation, tb_mods=mods)

    async def external(duel_id, player, said, operation, version):
        link = said.get("match")
        if not isinstance(link, str):
            raise web.HTTPBadRequest(text="match is required")
        return await series.external_room(factory_of(), duel_id, player, link, operation)

    async def unread(request, player, said):
        return {"notifications": await notify.unread(factory_of(), player)}

    async def seen(request, player, said):
        ids = said.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids) or len(ids) > 200:
            raise web.HTTPBadRequest(text="ids must be a list of numbers")
        return {"seen": await notify.seen(factory_of(), player, ids)}

    async def enter(request, player, said):
        return await service.enter_queue(factory_of(), player, _operation(request, said))

    async def leave(request, player, said):
        return await service.leave_queue(factory_of(), player, _operation(request, said))

    async def one(request, player, said):
        from db.models.duel import Duel

        async with factory_of()() as session:
            duel = await session.get(Duel, request.match_info["duel_id"])
            if duel is None:
                raise DuelError("NOT_FOUND")
            people = await service._participants(session, duel.id)
            if all(person.player_id != player for person in people):
                raise DuelError("NOT_PARTICIPANT")
            return await series.view(session, duel, player)

    async def mine(request, player, said):
        return {"duels": await service.mine(factory_of(), player)}

    async def events(request, player, said):
        after = request.query.get("after", "0")
        if not after.isdigit():
            raise web.HTTPBadRequest(text="after must be a number")
        return {"events": await service.events_after(factory_of(), request.match_info["duel_id"], player, int(after))}

    return [
        live.socket_route(guard, who, factory_of),
        web.post("/render/duels/invitations", handler(invite, 201)),
        web.get("/render/duels", handler(mine)),
        web.get("/render/duels/notifications", handler(unread)),
        web.post("/render/duels/notifications/seen", handler(seen)),
        web.post("/render/duels/queue", handler(enter)),
        web.delete("/render/duels/queue", handler(leave)),
        web.get("/render/duels/{duel_id}", handler(one)),
        web.get("/render/duels/{duel_id}/events", handler(events)),
        web.post("/render/duels/{duel_id}/accept", handler(on_duel(accept))),
        web.post("/render/duels/{duel_id}/decline", handler(on_duel(decline))),
        web.post("/render/duels/{duel_id}/surrender", handler(on_duel(surrender))),
        web.post("/render/duels/{duel_id}/pool-ack", handler(on_duel(pool_ack))),
        web.post("/render/duels/{duel_id}/draft-actions", handler(on_duel(draft))),
        web.post("/render/duels/{duel_id}/ready", handler(on_duel(ready))),
        web.post("/render/duels/{duel_id}/bonus-tb", handler(on_duel(bonus))),
        web.post("/render/duels/{duel_id}/external-room", handler(on_duel(external))),
    ]
