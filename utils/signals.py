import asyncio
from typing import Optional

from sqlalchemy import text

from config.settings import DATABASE_URL
from utils.logger import get_logger

logger = get_logger("signals")

RETRY = 5.0


class Signals:
    def __init__(self, url: str = ""):
        self._url = url
        self._marks: dict[str, asyncio.Event] = {}
        self._channels: tuple[str, ...] = ()
        self._listening: Optional[asyncio.Task] = None
        self._more: Optional[asyncio.Event] = None

    @property
    def remote(self) -> bool:
        return self._url.startswith("postgresql")

    def mark(self, channel: str) -> asyncio.Event:
        return self._marks.setdefault(channel, asyncio.Event())

    def heard(self, channel: str) -> None:
        mark = self._marks.pop(channel, None)
        if mark is not None:
            mark.set()

    async def wait(self, mark: asyncio.Event, most: float) -> bool:
        try:
            await asyncio.wait_for(mark.wait(), timeout=most)
            return True
        except asyncio.TimeoutError:
            return False

    async def announce(self, session, channel: str) -> None:
        if self.remote:
            await session.execute(text("select pg_notify(:channel, '')"), {"channel": channel})

    def listen(self, *channels: str) -> None:
        wanted = tuple(dict.fromkeys(self._channels + channels))
        if wanted == self._channels:
            return
        self._channels = wanted
        if not self.remote:
            return
        if self._listening is None:
            self._listening = asyncio.create_task(self._listen(), name="signals")
        elif self._more is not None:
            self._more.set()

    async def stop(self) -> None:
        task, self._listening = self._listening, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def _told(self, _connection, _pid, channel, _payload) -> None:
        self.heard(channel)

    async def _listen(self) -> None:
        import asyncpg

        address = self._url.replace("postgresql+asyncpg://", "postgresql://", 1)
        while True:
            connection = None
            try:
                connection = await asyncpg.connect(address)
                gone = asyncio.get_running_loop().create_future()
                connection.add_termination_listener(lambda _c: gone.done() or gone.set_result(None))
                self._more = asyncio.Event()
                added: set[str] = set()
                while not gone.done():
                    self._more.clear()
                    for channel in self._channels:
                        if channel not in added:
                            await connection.add_listener(channel, self._told)
                            added.add(channel)
                            self.heard(channel)
                    more = asyncio.create_task(self._more.wait())
                    await asyncio.wait({gone, more}, return_when=asyncio.FIRST_COMPLETED)
                    more.cancel()
                logger.warning("the connection that listens for signals closed")
            except asyncio.CancelledError:
                if connection is not None and not connection.is_closed():
                    await connection.close()
                raise
            except Exception as exc:
                logger.warning("listening for signals failed: %s", exc)
            await asyncio.sleep(RETRY)


shared = Signals(DATABASE_URL)
