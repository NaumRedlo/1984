import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import CallbackQuery, Message

from utils.i18n import t
from utils.language import get_language
from utils.ttl_cache import TTLCache


@dataclass(frozen=True)
class Policy:
    interval: float
    count: int
    window: float


POLICIES = {
    "menu": Policy(0.3, 20, 10),
    "heavy": Policy(1.5, 5, 15),
    "mutation": Policy(1, 10, 15),
    "upload": Policy(1, 4, 60),
}
MAX_HEAVY_RUNNING = 8
LIGHT_COMMANDS = {"help", "start", "sts", "group", "switch", "whereami", "admin", "ap"}
MUTATION_COMMANDS = {"st", "register", "reg", "link", "relink", "unlink", "purgeuser"}
MUTATION_PREFIXES = (
    "st:lang:set:", "st:tt:set:", "st:tt:off:", "st:skin:",
    "st:acc:link", "st:acc:relink", "st:acc:unlink", "dmtenant:set:",
    "reglang:", "pair:", "purge_confirm:",
)
HEAVY_PREFIXES = ("wif:", "lbm:", "lb:", "rk:", "tpp|", "tt|f|", "tt|p|", "ap:r:")
CLOSE_ACTIONS = {"st:close"}
NOOP_ACTIONS = {"pg|noop", "tpp|x", "tt|x", "st:tt:nop"}


def category(event, data):
    marked = get_flag(data, "rate_limit")
    if marked:
        return marked
    if isinstance(event, CallbackQuery):
        action = event.data or ""
        if action in CLOSE_ACTIONS or action in NOOP_ACTIONS or action.startswith("purge_cancel:"):
            return "close"
        if action.startswith(MUTATION_PREFIXES):
            return "mutation"
        if action.startswith(HEAVY_PREFIXES):
            return "heavy"
        return "menu"
    if not isinstance(event, Message) or not event.text:
        return None
    trigger = data.get("trigger_args")
    command = data.get("command")
    if trigger:
        name = getattr(trigger, "trigger", event.text.split()[0]).lower()
    elif command or event.text.lstrip().startswith("/"):
        name = getattr(command, "command", event.text.strip().split()[0]).lstrip("/").split("@")[0].lower()
    else:
        return None
    if name in LIGHT_COMMANDS:
        return "menu"
    if name in MUTATION_COMMANDS:
        return "mutation"
    return "heavy"


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self):
        clock = lambda: time.monotonic()
        self._requests = TTLCache(maxsize=20000, ttl=60, clock=clock)
        self._notified = TTLCache(maxsize=20000, ttl=3, clock=clock)
        self._closed = TTLCache(maxsize=20000, ttl=10, clock=clock)
        self._running = set()
        self._heavy_running = 0

    def _wait(self, user_id, group):
        policy = POLICIES[group]
        now = time.monotonic()
        timestamps = self._requests.get((user_id, group), deque())
        while timestamps and timestamps[0] <= now - policy.window:
            timestamps.popleft()
        wait = max(0, timestamps[-1] + policy.interval - now) if timestamps else 0
        if len(timestamps) >= policy.count:
            wait = max(wait, timestamps[0] + policy.window - now)
        return wait, timestamps

    async def _reject(self, event, user_id, key, seconds=0):
        if user_id in self._notified:
            if isinstance(event, CallbackQuery):
                await event.answer()
            return
        self._notified[user_id] = True
        lang = (await get_language(user_id)).lower()
        text = t(key, lang, seconds=max(1, math.ceil(seconds)))
        await event.answer(text)

    async def __call__(self, handler: Callable, event: object, data: Dict[str, Any]) -> Any:
        group = category(event, data)
        person = getattr(event, "from_user", None)
        if group is None or person is None:
            return await handler(event, data)
        if group == "close":
            if isinstance(event, CallbackQuery) and (event.data or "") in NOOP_ACTIONS:
                await event.answer()
                return
            message = getattr(event, "message", None)
            chat = getattr(message, "chat", None)
            key = (person.id, "close", getattr(chat, "id", None), getattr(message, "message_id", None), event.data)
            if key in self._running or key in self._closed:
                await event.answer()
                return
            self._running.add(key)
            try:
                result = await handler(event, data)
                self._closed[key] = True
                return result
            finally:
                self._running.discard(key)

        key = (person.id, group)
        if key in self._running:
            await self._reject(event, person.id, "common.command_running")
            return
        wait, timestamps = self._wait(person.id, group)
        if wait:
            await self._reject(event, person.id, "common.commands_wait", wait)
            return
        if group == "heavy" and self._heavy_running >= MAX_HEAVY_RUNNING:
            await self._reject(event, person.id, "common.server_busy", 2)
            return
        timestamps.append(time.monotonic())
        self._requests[key] = timestamps
        self._running.add(key)
        if group == "heavy":
            self._heavy_running += 1
        try:
            return await handler(event, data)
        finally:
            self._running.discard(key)
            if group == "heavy":
                self._heavy_running -= 1
