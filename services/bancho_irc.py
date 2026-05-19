"""Bancho IRC client for multiplayer room management.

Connects to irc.ppy.sh and provides async interface for !mp commands.

Resilience features:
- Auto-reconnect on connection loss (single in-flight reconnect task).
- Idle-watchdog: self-pinged keep-alives detect silently dropped sockets.
- Per-channel event subscriptions so handlers can be cleaned up precisely.
- Outgoing rate limiter (~600ms gap) to stay under Bancho throttling.
- on_reconnect hooks so callers can re-JOIN channels and re-arm handlers.
"""

import asyncio
import re
import time
from typing import Optional, Callable, Awaitable

from config.settings import OSU_IRC_USERNAME, OSU_IRC_PASSWORD
from utils.logger import get_logger

logger = get_logger("services.bancho_irc")

IRC_HOST = "irc.ppy.sh"
IRC_PORT = 6667
RECONNECT_DELAY = 15
SEND_GAP_SECONDS = 0.6           # ~10 msg / 6 s; safely under Bancho throttle
IDLE_PING_AFTER = 90.0           # seconds of silence before we send PING ourselves
IDLE_PONG_TIMEOUT = 30.0         # seconds to wait for any line after our PING
MP_MAKE_TIMEOUT = 30.0           # BanchoBot can be slow under load


class BanchoIRC:
    def __init__(self):
        self.username = OSU_IRC_USERNAME
        self.password = OSU_IRC_PASSWORD
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._read_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._shutdown = False
        # Global event handlers: list of (channel_or_None, callable).
        # When channel is None the handler fires for every channel.
        self._handlers: dict[str, list[tuple[Optional[str], Callable]]] = {}
        self._pending_responses: dict[str, asyncio.Future] = {}
        # Callbacks fired after a successful (re)connect handshake.
        self._on_reconnect_cbs: list[Callable[[], Awaitable[None]]] = []
        # Outgoing send serialization + throttle.
        self._send_lock = asyncio.Lock()
        self._last_send_at = 0.0
        # Last time we received any byte from the server (for idle watchdog).
        self._last_recv_at = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        if not self.username or not self.password:
            logger.warning("IRC credentials not configured, skipping connection")
            return False

        try:
            self._reader, self._writer = await asyncio.open_connection(IRC_HOST, IRC_PORT)
            self._last_recv_at = time.monotonic()
            self._send_raw(f"PASS {self.password}")
            self._send_raw(f"NICK {self.username}")
            self._send_raw(f"USER {self.username} 0 * :{self.username}")

            # Wait for welcome (001) or error
            welcome = await self._wait_for_welcome()
            if not welcome:
                logger.error("IRC: failed to receive welcome message")
                await self._teardown_socket()
                return False

            self._connected = True
            self._read_task = asyncio.create_task(self._read_loop(), name="bancho_irc_read")
            self._watchdog_task = asyncio.create_task(self._idle_watchdog(), name="bancho_irc_watchdog")
            logger.info(f"IRC: connected as {self.username}")

            # Fire on_reconnect hooks (non-blocking; failures must not abort connect).
            for cb in list(self._on_reconnect_cbs):
                try:
                    asyncio.create_task(cb(), name="bancho_irc_on_reconnect")
                except Exception as e:
                    logger.error(f"IRC: on_reconnect schedule failed: {e}")

            return True
        except Exception as e:
            logger.error(f"IRC: connection failed: {e}")
            await self._teardown_socket()
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
                self._last_recv_at = time.monotonic()
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
        self._shutdown = True
        self._connected = False
        for t in (self._reconnect_task, self._watchdog_task, self._read_task):
            if t and not t.done():
                t.cancel()
        await self._teardown_socket(send_quit=True)
        logger.info("IRC: disconnected")

    async def _teardown_socket(self, send_quit: bool = False):
        if self._writer:
            try:
                if send_quit:
                    self._send_raw("QUIT :bye")
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    def _send_raw(self, message: str):
        if self._writer:
            try:
                self._writer.write(f"{message}\r\n".encode("utf-8"))
            except Exception as e:
                logger.warning(f"IRC: write failed: {e}")

    async def _send(self, message: str):
        """Throttled async send. Serializes writes and enforces a minimum
        SEND_GAP_SECONDS gap so we stay well under Bancho's PRIVMSG limits."""
        async with self._send_lock:
            now = time.monotonic()
            gap = now - self._last_send_at
            if gap < SEND_GAP_SECONDS:
                await asyncio.sleep(SEND_GAP_SECONDS - gap)
            self._send_raw(message)
            if self._writer:
                try:
                    await self._writer.drain()
                except Exception as e:
                    logger.warning(f"IRC: drain failed: {e}")
            self._last_send_at = time.monotonic()

    async def send_pm(self, target: str, message: str):
        await self._send(f"PRIVMSG {target} :{message}")

    async def _read_loop(self):
        try:
            while self._connected and self._reader:
                try:
                    line = await self._reader.readline()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"IRC read error: {e}")
                    break
                if not line:
                    break
                self._last_recv_at = time.monotonic()
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                logger.debug(f"IRC<< {decoded}")

                if decoded.startswith("PING"):
                    pong = decoded.replace("PING", "PONG", 1)
                    await self._send(pong)
                    continue
                # Server's PONG reply to our keep-alive — _last_recv_at already updated.
                if " PONG " in decoded or decoded.startswith("PONG"):
                    continue

                asyncio.create_task(self._safe_handle(decoded), name="bancho_irc_handle")
        except asyncio.CancelledError:
            return
        finally:
            self._on_disconnect("read loop exit")

    async def _safe_handle(self, raw: str):
        try:
            await self._handle_message(raw)
        except Exception as e:
            logger.error(f"IRC handler crashed on '{raw[:200]}': {e}", exc_info=True)

    async def _idle_watchdog(self):
        """Detect silently dropped sockets: if quiet > IDLE_PING_AFTER, send a
        keep-alive PING; if no incoming line within IDLE_PONG_TIMEOUT after
        that, force reconnect."""
        try:
            while self._connected:
                await asyncio.sleep(15)
                if not self._connected:
                    return
                idle = time.monotonic() - self._last_recv_at
                if idle < IDLE_PING_AFTER:
                    continue

                ping_sent_at = time.monotonic()
                try:
                    await self._send(f"PING :{int(time.time())}")
                except Exception as e:
                    logger.warning(f"IRC watchdog: ping send failed: {e}")
                    self._force_disconnect("watchdog ping send failed")
                    return

                # Wait for ANY incoming line newer than ping_sent_at.
                deadline = ping_sent_at + IDLE_PONG_TIMEOUT
                got_reply = False
                while time.monotonic() < deadline:
                    await asyncio.sleep(2)
                    if not self._connected:
                        return
                    if self._last_recv_at > ping_sent_at:
                        got_reply = True
                        break
                if not got_reply:
                    logger.warning(
                        f"IRC watchdog: no traffic for {IDLE_PING_AFTER + IDLE_PONG_TIMEOUT:.0f}s — reconnecting"
                    )
                    self._force_disconnect("watchdog idle timeout")
                    return
        except asyncio.CancelledError:
            return

    def _force_disconnect(self, reason: str):
        """Close socket from outside the read loop; read loop will exit and
        trigger _on_disconnect → reconnect."""
        logger.info(f"IRC: force disconnect ({reason})")
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass

    def _on_disconnect(self, why: str):
        """Called once when the read loop exits. Schedules a reconnect unless
        we're shutting down."""
        was_connected = self._connected
        self._connected = False
        if self._shutdown:
            return
        if not was_connected:
            # Could be an early read failure during handshake; we still want to
            # try reconnecting unless someone is already on it.
            pass
        logger.warning(f"IRC: connection lost ({why}), scheduling reconnect")
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop(), name="bancho_irc_reconnect")

    async def _reconnect_loop(self):
        try:
            while not self._connected and not self._shutdown:
                logger.info(f"IRC: attempting reconnect in {RECONNECT_DELAY}s")
                await asyncio.sleep(RECONNECT_DELAY)
                if self._shutdown:
                    return
                success = await self.connect()
                if success:
                    logger.info("IRC: reconnected successfully")
                    return
        except asyncio.CancelledError:
            return

    # ── Handler registry ─────────────────────────────────────────────────────

    def on(self, event: str, handler: Callable[..., Awaitable], channel: Optional[str] = None):
        """Subscribe to an event. If `channel` is provided, the handler only
        fires for that channel (case-insensitive). Otherwise it fires for all."""
        ch = channel.lower() if channel else None
        self._handlers.setdefault(event, []).append((ch, handler))

    def off(self, event: str, handler: Callable[..., Awaitable], channel: Optional[str] = None):
        """Remove a previously registered handler. Safe to call if not present."""
        ch = channel.lower() if channel else None
        bucket = self._handlers.get(event, [])
        for i, (existing_ch, existing_h) in enumerate(bucket):
            if existing_h is handler and existing_ch == ch:
                bucket.pop(i)
                return

    def drop_channel_handlers(self, channel: str, event: Optional[str] = None):
        """Remove handlers bound to a channel. If `event` is given, drop only
        that event's handlers for the channel; otherwise drop every handler
        bound to the channel (across all events). Used by close_room and by
        the reconnect re-arm flow."""
        ch = channel.lower()
        events = [event] if event else list(self._handlers.keys())
        for ev in events:
            bucket = self._handlers.get(ev)
            if not bucket:
                continue
            self._handlers[ev] = [(c, h) for (c, h) in bucket if c != ch]

    def add_on_reconnect(self, cb: Callable[[], Awaitable[None]]):
        """Register a callback to run after every successful (re)connect."""
        self._on_reconnect_cbs.append(cb)

    def _fire(self, event: str, channel: str, *args):
        ch = channel.lower()
        for handler_ch, handler in list(self._handlers.get(event, [])):
            if handler_ch is not None and handler_ch != ch:
                continue
            asyncio.create_task(handler(channel, *args), name=f"bancho_irc_{event}")

    # ── Message parsing ──────────────────────────────────────────────────────

    async def _handle_message(self, raw: str):
        # Parse PRIVMSG
        pm_match = re.match(r":(\S+)!\S+ PRIVMSG (\S+) :(.+)", raw)
        if pm_match:
            sender = pm_match.group(1)
            target = pm_match.group(2)
            text = pm_match.group(3)

            if target.startswith("#"):
                await self._handle_channel_message(sender, target, text)
            elif sender.lower() == "banchobot":
                await self._handle_banchobot(text, target)
            return

    async def _handle_banchobot(self, text: str, target: str):
        # Room created: "Created the tournament match https://osu.ppy.sh/mp/12345"
        room_match = re.search(r"Created the tournament match https://osu\.ppy\.sh/mp/(\d+)", text)
        if room_match:
            match_id = int(room_match.group(1))
            for key in list(self._pending_responses):
                if key.startswith("mp_make_"):
                    fut = self._pending_responses.pop(key)
                    if not fut.done():
                        fut.set_result(match_id)
                    break
            return

        # mp_settings replies start with "Room name:" — match the first waiting
        # request and hand it off (per-call keys avoid cross-duel collisions).
        if "Room name:" in text:
            for key in list(self._pending_responses):
                if key.startswith("mp_settings_"):
                    fut = self._pending_responses.pop(key)
                    if not fut.done():
                        fut.set_result(text)
                    break

    async def _handle_channel_message(self, sender: str, channel: str, text: str):
        if sender.lower() != "banchobot":
            return

        # Also feed BanchoBot channel messages through _handle_banchobot for
        # futures (mp_settings can arrive in-channel after a JOIN).
        await self._handle_banchobot(text, channel)

        if "finished playing" in text.lower():
            self._fire("match_finished", channel, text)
            return

        if "All players are ready" in text:
            self._fire("all_ready", channel, text)
            return

        if "left the game" in text:
            m = re.match(r"(\S+) left the game", text)
            if m:
                self._fire("player_left", channel, m.group(1))
            return

    # ── !mp commands ─────────────────────────────────────────────────────────

    async def mp_make(self, room_name: str, timeout: float = MP_MAKE_TIMEOUT) -> Optional[int]:
        fut = asyncio.get_event_loop().create_future()
        key = f"mp_make_{id(fut)}"
        self._pending_responses[key] = fut
        await self.send_pm("BanchoBot", f"!mp make {room_name}")
        try:
            match_id = await asyncio.wait_for(fut, timeout=timeout)
            return match_id
        except asyncio.TimeoutError:
            self._pending_responses.pop(key, None)
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
        key = f"mp_settings_{id(fut)}"
        self._pending_responses[key] = fut
        await self._send(f"PRIVMSG {channel} :!mp settings")
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_responses.pop(key, None)
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
