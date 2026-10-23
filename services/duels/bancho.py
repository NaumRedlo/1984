import asyncio
import time
from typing import Awaitable, Callable, Optional, Protocol

from services.duels.bancho_text import BANCHO_BOT, Said, match_channel, parse_line, said
from utils.logger import get_logger

logger = get_logger("duels.bancho")

SEND_EVERY = 0.6
FIRST_RETRY = 2.0
LONGEST_RETRY = 120.0
SILENCE = 300.0

class Transport(Protocol):
    async def open(self) -> None: ...
    async def readline(self) -> Optional[str]: ...
    async def write(self, line: str) -> None: ...
    async def close(self) -> None: ...

class TcpTransport:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

    async def open(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

    async def readline(self) -> Optional[str]:
        assert self.reader is not None
        raw = await self.reader.readline()
        return raw.decode("utf-8", errors="replace") if raw else None

    async def write(self, line: str) -> None:
        assert self.writer is not None
        self.writer.write((line + "\r\n").encode("utf-8"))
        await self.writer.drain()

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        self.reader = self.writer = None

Listener = Callable[[Said], Awaitable[None]]

class Gateway:
    def __init__(self, username: str, password: str, transport: Callable[[], Transport], send_every: float = SEND_EVERY,
                 clock: Callable[[], float] = time.monotonic):
        self.username = username
        self.password = password
        self.make_transport = transport
        self.send_every = send_every
        self.clock = clock
        self.listeners: list[Listener] = []
        self.channels: set[str] = set()
        self.outgoing: asyncio.Queue[str] = asyncio.Queue()
        self.connected = asyncio.Event()
        self.transport: Optional[Transport] = None
        self.waiting: dict[str, asyncio.Future] = {}
        self.last_heard = clock()

    def listen(self, listener: Listener) -> None:
        self.listeners.append(listener)

    async def say(self, target: str, text: str) -> None:
        await self.outgoing.put(f"PRIVMSG {target} :{text}")

    async def join(self, channel: str) -> None:
        self.channels.add(channel)
        if self.connected.is_set():
            await self.outgoing.put(f"JOIN {channel}")

    async def leave(self, channel: str) -> None:
        self.channels.discard(channel)
        if self.connected.is_set():
            await self.outgoing.put(f"PART {channel}")

    async def make(self, name: str, timeout: float = 30.0) -> Optional[int]:
        future = asyncio.get_running_loop().create_future()
        self.waiting[name] = future
        try:
            await self.say(BANCHO_BOT, f"!mp make {name}")
            match_id = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.waiting.pop(name, None)
        await self.join(match_channel(match_id))
        return match_id

    async def handle(self, raw: str) -> None:
        line = parse_line(raw)
        if line is None:
            return
        self.last_heard = self.clock()
        if line.command == "PING":
            await self._write(f"PONG :{line.text or (line.params[0] if line.params else '')}")
            return
        if line.command == "001":
            self.connected.set()
            for channel in sorted(self.channels):
                await self.outgoing.put(f"JOIN {channel}")
            return
        if line.command == "464":
            raise PermissionError("Bancho refused the IRC password")
        event = said(line)
        if event is None:
            return
        if event.kind == "created":
            future = self.waiting.get(event.data["name"])
            if future is not None and not future.done():
                future.set_result(event.data["match"])
        for listener in list(self.listeners):
            try:
                await listener(event)
            except Exception as exc:
                logger.error("a Bancho listener failed on %r: %s", event.raw, exc, exc_info=True)

    async def _write(self, line: str) -> None:
        if self.transport is not None:
            await self.transport.write(line)

    async def _send_loop(self) -> None:
        last = -self.send_every
        while True:
            line = await self.outgoing.get()
            await self.connected.wait()
            wait = self.send_every - (self.clock() - last)
            if wait > 0:
                await asyncio.sleep(wait)
            await self._write(line)
            last = self.clock()

    async def session(self) -> None:
        self.transport = self.make_transport()
        await self.transport.open()
        await self.transport.write(f"PASS {self.password}")
        await self.transport.write(f"NICK {self.username}")
        await self.transport.write(f"USER {self.username} 0 * :{self.username}")
        sender = asyncio.create_task(self._send_loop())
        try:
            while True:
                raw = await asyncio.wait_for(self.transport.readline(), SILENCE)
                if raw is None:
                    return
                await self.handle(raw)
        finally:
            sender.cancel()
            self.connected.clear()
            await self.transport.close()
            self.transport = None

    async def run(self, shutdown: asyncio.Event) -> None:
        pause = FIRST_RETRY
        while not shutdown.is_set():
            started = self.clock()
            try:
                await self.session()
            except PermissionError as exc:
                logger.error("%s; duels stay without Bancho", exc)
                return
            except (OSError, asyncio.TimeoutError) as exc:
                logger.warning("Bancho connection lost: %s", exc)
            if self.clock() - started > 60:
                pause = FIRST_RETRY
            try:
                await asyncio.wait_for(shutdown.wait(), pause)
                return
            except asyncio.TimeoutError:
                pause = min(pause * 2, LONGEST_RETRY)
