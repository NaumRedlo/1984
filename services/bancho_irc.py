"""Bancho IRC client for multiplayer room management.

Connects to irc.ppy.sh and provides async interface for !mp commands.
"""

import asyncio
import re
from typing import Optional, Callable, Awaitable

from config.settings import OSU_IRC_USERNAME, OSU_IRC_PASSWORD
from utils.logger import get_logger

logger = get_logger("services.bancho_irc")

IRC_HOST = "irc.ppy.sh"
IRC_PORT = 6667
RECONNECT_DELAY = 15


class BanchoIRC:
    def __init__(self):
        self.username = OSU_IRC_USERNAME
        self.password = OSU_IRC_PASSWORD
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._read_task: Optional[asyncio.Task] = None
        self._handlers: dict[str, list[Callable]] = {}
        self._pending_responses: dict[str, asyncio.Future] = {}
        self._mp_channel_map: dict[int, str] = {}

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        if not self.username or not self.password:
            logger.warning("IRC credentials not configured, skipping connection")
            return False

        try:
            self._reader, self._writer = await asyncio.open_connection(IRC_HOST, IRC_PORT)
            self._send_raw(f"PASS {self.password}")
            self._send_raw(f"NICK {self.username}")
            self._send_raw(f"USER {self.username} 0 * :{self.username}")

            # Wait for welcome (001) or error
            welcome = await self._wait_for_welcome()
            if not welcome:
                logger.error("IRC: failed to receive welcome message")
                await self.disconnect()
                return False

            self._connected = True
            self._read_task = asyncio.create_task(self._read_loop(), name="bancho_irc_read")
            logger.info(f"IRC: connected as {self.username}")
            return True
        except Exception as e:
            logger.error(f"IRC: connection failed: {e}")
            return False

    async def _wait_for_welcome(self, timeout: float = 15.0) -> bool:
        try:
            end = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < end:
                line = await asyncio.wait_for(
                    self._reader.readline(), timeout=end - asyncio.get_event_loop().time()
                )
                if not line:
                    return False
                decoded = line.decode("utf-8", errors="replace").strip()
                if " 001 " in decoded:
                    return True
                if "ERROR" in decoded.upper():
                    logger.error(f"IRC: server error: {decoded}")
                    return False
        except asyncio.TimeoutError:
            return False
        return False

    async def disconnect(self):
        self._connected = False
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if self._writer:
            try:
                self._send_raw("QUIT :bye")
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        logger.info("IRC: disconnected")

    def _send_raw(self, message: str):
        if self._writer:
            self._writer.write(f"{message}\r\n".encode("utf-8"))

    async def _send(self, message: str):
        self._send_raw(message)
        if self._writer:
            await self._writer.drain()

    async def send_pm(self, target: str, message: str):
        await self._send(f"PRIVMSG {target} :{message}")

    async def _read_loop(self):
        while self._connected and self._reader:
            try:
                line = await self._reader.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                if decoded.startswith("PING"):
                    pong = decoded.replace("PING", "PONG", 1)
                    await self._send(pong)
                    continue

                asyncio.create_task(self._handle_message(decoded))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"IRC read error: {e}")
                break

        if self._connected:
            self._connected = False
            logger.warning("IRC: connection lost, will reconnect")

    async def _handle_message(self, raw: str):
        # Parse PRIVMSG from BanchoBot
        pm_match = re.match(
            r":(\S+)!\S+ PRIVMSG (\S+) :(.+)", raw
        )
        if pm_match:
            sender = pm_match.group(1)
            target = pm_match.group(2)
            text = pm_match.group(3)

            if sender.lower() == "banchobot":
                await self._handle_banchobot(text, target)
            return

        # Channel messages (from #mp_ channels)
        chan_match = re.match(
            r":(\S+)!\S+ PRIVMSG (#\S+) :(.+)", raw
        )
        if chan_match:
            sender = chan_match.group(1)
            channel = chan_match.group(2)
            text = chan_match.group(3)
            await self._handle_channel_message(sender, channel, text)

    async def _handle_banchobot(self, text: str, target: str):
        # Room created: "Created the tournament match https://osu.ppy.sh/mp/12345"
        room_match = re.search(r"Created the tournament match https://osu\.ppy\.sh/mp/(\d+)", text)
        if room_match:
            match_id = int(room_match.group(1))
            fut = self._pending_responses.pop("mp_make", None)
            if fut and not fut.done():
                fut.set_result(match_id)
            return

        # Forward to any waiting futures by key patterns
        for key, fut in list(self._pending_responses.items()):
            if not fut.done():
                if key == "mp_settings" and "Room name:" in text:
                    fut.set_result(text)
                    self._pending_responses.pop(key, None)

    async def _handle_channel_message(self, sender: str, channel: str, text: str):
        # Match finished detection
        if sender.lower() == "banchobot" and "finished playing" in text.lower():
            for handler in self._handlers.get("match_finished", []):
                asyncio.create_task(handler(channel, text))

        # All players ready
        if sender.lower() == "banchobot" and "All players are ready" in text:
            for handler in self._handlers.get("all_ready", []):
                asyncio.create_task(handler(channel, text))

    def on(self, event: str, handler: Callable[..., Awaitable]):
        self._handlers.setdefault(event, []).append(handler)

    # ── !mp commands ─────────────────────────────────────────────────────────

    async def mp_make(self, room_name: str, timeout: float = 10.0) -> Optional[int]:
        fut = asyncio.get_event_loop().create_future()
        self._pending_responses["mp_make"] = fut
        await self.send_pm("BanchoBot", f"!mp make {room_name}")
        try:
            match_id = await asyncio.wait_for(fut, timeout=timeout)
            return match_id
        except asyncio.TimeoutError:
            self._pending_responses.pop("mp_make", None)
            logger.warning("IRC: mp_make timed out")
            return None

    async def mp_invite(self, channel: str, username: str):
        await self._send(f"PRIVMSG {channel} :!mp invite {username}")

    async def mp_map(self, channel: str, beatmap_id: int, mode: int = 0):
        await self._send(f"PRIVMSG {channel} :!mp map {beatmap_id} {mode}")

    async def mp_mods(self, channel: str, mods: str = "Freemod"):
        await self._send(f"PRIVMSG {channel} :!mp mods {mods}")

    async def mp_start(self, channel: str, countdown: int = 10):
        await self._send(f"PRIVMSG {channel} :!mp start {countdown}")

    async def mp_abort(self, channel: str):
        await self._send(f"PRIVMSG {channel} :!mp abort")

    async def mp_close(self, channel: str):
        await self._send(f"PRIVMSG {channel} :!mp close")

    async def mp_set(self, channel: str, team_mode: int = 0, score_mode: int = 0, size: int = 2):
        await self._send(f"PRIVMSG {channel} :!mp set {team_mode} {score_mode} {size}")

    async def mp_password(self, channel: str, password: str = ""):
        await self._send(f"PRIVMSG {channel} :!mp password {password}")

    async def mp_settings(self, channel: str, timeout: float = 5.0) -> Optional[str]:
        fut = asyncio.get_event_loop().create_future()
        self._pending_responses["mp_settings"] = fut
        await self._send(f"PRIVMSG {channel} :!mp settings")
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_responses.pop("mp_settings", None)
            return None

    async def join_channel(self, channel: str):
        await self._send(f"JOIN {channel}")


# Singleton
_irc_client: Optional[BanchoIRC] = None


def get_irc_client() -> BanchoIRC:
    global _irc_client
    if _irc_client is None:
        _irc_client = BanchoIRC()
    return _irc_client
