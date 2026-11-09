import asyncio
from typing import Any, Callable, Iterable, Optional

from aiohttp import WSMsgType, web

from services.render_farm import invites
from utils.logger import get_logger

logger = get_logger("render_farm.events")

TOPICS = ("duels", "live", "online")
BEAT_EVERY = 10.0
SEND_PATIENCE = 2.0
SOCKETS_MOST = 4

class Events:
    def __init__(self) -> None:
        self.sockets: dict[web.WebSocketResponse, tuple[str, Optional[int]]] = {}

    def join(self, socket: web.WebSocketResponse, token: str, player_id: Optional[int]) -> list[web.WebSocketResponse]:
        earlier = [held for held, (known, _) in self.sockets.items() if known == token]
        self.sockets[socket] = (token, player_id)
        return earlier[:max(0, len(earlier) + 1 - SOCKETS_MOST)]

    def leave(self, socket: web.WebSocketResponse) -> None:
        self.sockets.pop(socket, None)

    def connected(self, player_id: int) -> bool:
        return any(known == player_id and not socket.closed for socket, (_, known) in self.sockets.items())

    async def _send(self, socket: web.WebSocketResponse, message: dict[str, Any]) -> bool:
        try:
            await asyncio.wait_for(socket.send_json(message), SEND_PATIENCE)
            return True
        except (ConnectionError, RuntimeError):
            self.leave(socket)
        except asyncio.TimeoutError:
            self.leave(socket)
            logger.warning("closing an event socket that did not take a message within %.0f s", SEND_PATIENCE)
            asyncio.create_task(socket.close())
        return False

    async def tell(self, players: Optional[Iterable[int]], topic: str) -> int:
        wanted = None if players is None else set(players)
        sockets = [socket for socket, (_, player_id) in list(self.sockets.items())
                   if not socket.closed and (wanted is None or player_id in wanted)]
        sent = await asyncio.gather(*(self._send(socket, {"type": "changed", "topic": topic}) for socket in sockets))
        return sum(sent)

hub = Events()

def socket_route(guard, events_of: Callable[[], Events] = lambda: hub, beat_every: float = BEAT_EVERY) -> web.RouteDef:
    async def stream(request: web.Request) -> web.StreamResponse:
        denied = await guard(request)
        if denied is not None:
            return denied
        header = request.headers.get("Authorization", "")
        token = header[len("Bearer "):] if header.startswith("Bearer ") else ""
        owner = invites.owner(token)
        if owner is None:
            return web.json_response({"error": "no one"}, status=404)
        events = events_of()
        socket = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 12)
        await socket.prepare(request)
        for extra in events.join(socket, token, owner.player_id):
            asyncio.create_task(extra.close())
        invites.touch(token, keep=invites.PRESENCE_BEAT_FOR)
        told = invites.online()
        await socket.send_json({"type": "hello", "topics": list(TOPICS), "online": told})

        async def beat() -> None:
            nonlocal told
            while not socket.closed:
                await asyncio.sleep(beat_every)
                invites.touch(token, keep=invites.PRESENCE_BEAT_FOR)
                now = invites.online()
                if now != told and await events._send(socket, {"type": "online", "online": now}):
                    told = now

        beating = asyncio.create_task(beat())
        try:
            async for message in socket:
                if message.type == WSMsgType.TEXT and message.data == "bye":
                    break
        finally:
            beating.cancel()
            events.leave(socket)
        return socket

    return web.get("/render/events", stream)
