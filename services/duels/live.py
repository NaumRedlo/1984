import asyncio
import math
import time
from typing import Any, Awaitable, Callable, Optional

from aiohttp import WSMsgType, web
from sqlalchemy import select

from db.models.duel import Duel, DuelEvent, DuelParticipant
from services.duels import series, states
from services.duels.rules import PARAMETERS
from utils.logger import get_logger

logger = get_logger("duels.live")

FRAMES_MOST = 2000
STATE_KEYS = {"beatmap_id", "mods", "time", "score", "combo", "counts", "accuracy", "screen"}
WITNESS_LOSSES = {"quit", "left_map"}
PUMP_EVERY = 0.5
LIVE = (states.PREPARING, states.DRAFTING, states.SELECTING, states.ROUND_SETUP, states.WAITING_READY, states.STARTING,
        states.PLAYING, states.WAITING_PLAYERS)

def _stamp(moment) -> Optional[str]:
    return moment.isoformat() + "Z" if moment is not None else None

def frames_ok(frames: Any) -> bool:
    if not isinstance(frames, list) or not frames or len(frames) > FRAMES_MOST:
        return False
    for frame in frames:
        if not isinstance(frame, list) or len(frame) != 4:
            return False
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in frame):
            return False
        if not -1024 <= frame[1] <= 1536 or not -768 <= frame[2] <= 1152 or not 0 <= frame[3] <= 31:
            return False
    return True

def witness_state(state: Any) -> Optional[dict[str, Any]]:
    if not isinstance(state, dict):
        return None
    kept = {key: value for key, value in state.items() if key in STATE_KEYS}
    for key in ("beatmap_id", "time", "score", "combo"):
        if key in kept and (isinstance(kept[key], bool) or not isinstance(kept[key], (int, float))):
            return None
    return kept

class Hub:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.watching: dict[str, set[web.WebSocketResponse]] = {}
        self.owners: dict[web.WebSocketResponse, int] = {}
        self.seen: dict[int, float] = {}
        self.lost: set[tuple[str, int]] = set()
        self.cursor: Optional[int] = None
        self.states: dict[str, dict[int, dict[str, Any]]] = {}

    def witnessed(self, duel_id: str) -> dict[int, dict[str, Any]]:
        return dict(self.states.get(duel_id, {}))

    def heard(self, player_id: int) -> None:
        self.seen[player_id] = self.clock()

    def present(self, player_id: int) -> bool:
        last = self.seen.get(player_id)
        return last is not None and self.clock() - last <= PARAMETERS["app_absence_seconds"]

    async def send(self, duel_id: str, message: dict[str, Any], skip: Optional[web.WebSocketResponse] = None) -> None:
        for socket in list(self.watching.get(duel_id, ())):
            if socket is skip or socket.closed:
                continue
            try:
                await socket.send_json(message)
            except (ConnectionError, RuntimeError):
                self.watching[duel_id].discard(socket)

    def forget(self, socket: web.WebSocketResponse) -> None:
        for sockets in self.watching.values():
            sockets.discard(socket)
        self.owners.pop(socket, None)

    async def pump(self, factory) -> int:
        async with factory() as session:
            if self.cursor is None:
                self.cursor = await session.scalar(select(DuelEvent.id).order_by(DuelEvent.id.desc()).limit(1)) or 0
                return 0
            rows = (await session.scalars(select(DuelEvent).where(DuelEvent.id > self.cursor).order_by(DuelEvent.id).limit(500))).all()
        for row in rows:
            self.cursor = row.id
            await self.send(row.duel_id, {"type": "event", "duel": row.duel_id, "sequence": row.sequence, "event": row.type,
                                          "payload": row.payload, "version": row.state_version, "at": _stamp(row.occurred_at),
                                          "server_time": _stamp(row.received_at)})
        return len(rows)

    async def watch_presence(self, factory, lose: Callable[..., Awaitable[Any]] = series.player_lost,
                             back: Callable[..., Awaitable[Any]] = series.player_back) -> None:
        async with factory() as session:
            rows = (await session.execute(
                select(Duel.id, Duel.state, DuelParticipant.player_id).join(DuelParticipant, DuelParticipant.duel_id == Duel.id)
                .where(Duel.state.in_(LIVE + (states.PAUSED,)))
            )).all()
        for duel_id, state, player_id in rows:
            key = (duel_id, player_id)
            if state in LIVE and state != states.WAITING_PLAYERS and player_id in self.seen and not self.present(player_id):
                if key not in self.lost:
                    self.lost.add(key)
                    await lose(factory, duel_id, player_id)
            elif state == states.PAUSED and key in self.lost and self.present(player_id):
                self.lost.discard(key)
                await back(factory, duel_id, player_id)

    async def run(self, factory, shutdown: asyncio.Event) -> None:
        while not shutdown.is_set():
            try:
                await self.pump(factory)
                await self.watch_presence(factory)
            except Exception as exc:
                logger.error("the duel hub stumbled: %s", exc, exc_info=True)
            try:
                await asyncio.wait_for(shutdown.wait(), PUMP_EVERY)
            except asyncio.TimeoutError:
                pass

hub = Hub()

async def _participant(factory, duel_id: str, player_id: int) -> bool:
    async with factory() as session:
        return await session.scalar(select(DuelParticipant.id).where(DuelParticipant.duel_id == duel_id,
                                                                     DuelParticipant.player_id == player_id)) is not None

async def _exists(factory, duel_id: str) -> bool:
    async with factory() as session:
        return await session.get(Duel, duel_id) is not None

def socket_route(guard, who, factory_of: Callable[[], Any], hub_of: Callable[[], Hub] = lambda: hub,
                 lose: Callable[..., Awaitable[Any]] = series.player_lost) -> web.RouteDef:
    async def stream(request: web.Request) -> web.StreamResponse:
        denied = await guard(request)
        if denied is not None:
            return denied
        owner = await who(request)
        if owner is None or owner.player_id is None:
            return web.json_response({"error": "NOT_LINKED"}, status=403)
        factory = factory_of()
        live = hub_of()
        socket = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
        await socket.prepare(request)
        live.owners[socket] = owner.player_id
        live.heard(owner.player_id)
        try:
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    said = message.json()
                except ValueError:
                    continue
                if not isinstance(said, dict):
                    continue
                await _handle(live, factory, socket, owner.player_id, said, lose)
        finally:
            live.forget(socket)
        return socket

    return web.get("/render/duels/live", stream)

async def _handle(live: Hub, factory, socket, player_id: int, said: dict[str, Any], lose) -> None:
    kind = said.get("type")
    duel_id = said.get("duel")
    live.heard(player_id)
    if kind == "heartbeat":
        await socket.send_json({"type": "heartbeat", "server_time": _stamp_now()})
        return
    if not isinstance(duel_id, str) or len(duel_id) > 36:
        return
    if kind == "watch":
        if await _exists(factory, duel_id):
            live.watching.setdefault(duel_id, set()).add(socket)
            await socket.send_json({"type": "watching", "duel": duel_id})
        return
    if kind == "unwatch":
        live.watching.get(duel_id, set()).discard(socket)
        return
    if not await _participant(factory, duel_id, player_id):
        await socket.send_json({"type": "error", "error": "NOT_PARTICIPANT", "duel": duel_id})
        return
    if kind == "frames" and frames_ok(said.get("frames")):
        await live.send(duel_id, {"type": "frames", "duel": duel_id, "player": player_id, "round": said.get("round"),
                                  "frames": said["frames"]}, skip=socket)
    elif kind == "witness":
        state = witness_state(said.get("state"))
        if state is not None:
            live.states.setdefault(duel_id, {})[player_id] = state
            await live.send(duel_id, {"type": "witness", "duel": duel_id, "player": player_id, "state": state}, skip=socket)
        if said.get("event") in WITNESS_LOSSES:
            live.lost.add((duel_id, player_id))
            await lose(factory, duel_id, player_id)
    elif kind == "bye":
        live.seen.pop(player_id, None)
        live.lost.add((duel_id, player_id))
        await lose(factory, duel_id, player_id)

def _stamp_now() -> str:
    from utils.timeutils import utcnow

    return utcnow().isoformat() + "Z"
