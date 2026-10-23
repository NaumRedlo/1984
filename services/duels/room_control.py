import asyncio
from typing import Optional

from services.duels.bancho_text import Said, Settings, match_channel, mods_line, problems, setup_lines

SETTINGS_WAIT = 10.0
REPLY_WAIT = 10.0

class RoomControl:
    def __init__(self, gateway, match_id: int):
        self.gateway = gateway
        self.match_id = match_id
        self.channel = match_channel(match_id)
        self.collecting: Optional[Settings] = None
        self.collected: Optional[asyncio.Future] = None
        self.expecting: dict[str, asyncio.Future] = {}
        self.events: asyncio.Queue[Said] = asyncio.Queue()
        gateway.listen(self.heard)

    async def heard(self, event: Said) -> None:
        if event.channel != self.channel:
            return
        if self.collecting is not None and self.collecting.take(event):
            if self.collecting.complete and self.collected is not None and not self.collected.done():
                self.collected.set_result(self.collecting)
            return
        future = self.expecting.get(event.kind)
        if future is not None and not future.done():
            future.set_result(event)
        await self.events.put(event)

    async def _expect(self, kind: str, line: str, wait: float = REPLY_WAIT) -> Optional[Said]:
        future = asyncio.get_running_loop().create_future()
        self.expecting[kind] = future
        try:
            await self.gateway.say(self.channel, line)
            return await asyncio.wait_for(future, wait)
        except asyncio.TimeoutError:
            return None
        finally:
            self.expecting.pop(kind, None)

    async def settings(self, wait: Optional[float] = None) -> Optional[Settings]:
        self.collecting = Settings()
        self.collected = asyncio.get_running_loop().create_future()
        try:
            await self.gateway.say(self.channel, "!mp settings")
            return await asyncio.wait_for(self.collected, SETTINGS_WAIT if wait is None else wait)
        except asyncio.TimeoutError:
            return None
        finally:
            self.collecting = None
            self.collected = None

    async def setup(self, password: str, players: list[str]) -> None:
        for line in setup_lines(password, players):
            await self.gateway.say(self.channel, line)

    async def prepare(self, beatmap: int, mods: list[str], free: bool) -> bool:
        changed = await self._expect("map_set", f"!mp map {beatmap} 0")
        moded = await self._expect("mods_set", mods_line(mods, free))
        return changed is not None and moded is not None

    async def check(self, beatmap: int, players: set[int], mods: set[str], free: bool) -> list[str]:
        settings = await self.settings()
        if settings is None:
            return ["no_settings"]
        return problems(settings, beatmap, players, mods, free)

    async def start(self, countdown: int = 5) -> None:
        await self.gateway.say(self.channel, f"!mp start {countdown}")

    async def abort(self) -> bool:
        return await self._expect("aborted", "!mp abort") is not None

    async def close(self) -> bool:
        return await self._expect("closed", "!mp close") is not None

    async def can_referee(self) -> bool:
        await self.gateway.join(self.channel)
        return await self.settings() is not None
