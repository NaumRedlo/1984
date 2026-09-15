import secrets
import time
from typing import NamedTuple, Optional

from services.render_farm import invites
from utils.logger import get_logger

logger = get_logger("services.render_farm.pairing")

GOOD_FOR = invites.GOOD_FOR

MOST_PENDING = 50
MOST_STARTS = 5
STARTS_WINDOW = 600.0
MOST_MISSES = 20
MISSES_WINDOW = 60.0

WAITING = "waiting"
LINKED = "linked"
GONE = "gone"
THROTTLED = "throttled"

class Machine(NamedTuple):
    name: str
    os: str = ""
    cores: int = 0
    build: str = ""

class Pairing(NamedTuple):
    machine: Machine
    expires_at: float
    linked_to: Optional[int] = None
    linked_name: str = ""

class Collected(NamedTuple):
    status: str
    token: Optional[str] = None

_pending: dict[str, Pairing] = {}
_starts: dict[str, list[float]] = {}
_misses: dict[str, list[float]] = {}

bot_username: str = ""

def set_bot_username(name: str) -> None:
    global bot_username
    bot_username = (name or "").lstrip("@")

def link(code: str) -> str:
    if not bot_username:
        return ""
    return f"https://t.me/{bot_username}?start=pair-{invites.tidy(code)}"

def start(machine: Machine, address: str) -> Optional[str]:
    _sweep()
    if not _within(_starts, address, MOST_STARTS, STARTS_WINDOW):
        logger.warning("too many pairings started from %s — refusing for now", address)
        return None
    if len(_pending) >= MOST_PENDING:
        logger.warning("too many pairings waiting — refusing for now")
        return None
    code = "".join(secrets.choice(invites.ALPHABET) for _ in range(invites.CODE_LENGTH))
    _pending[code] = Pairing(machine, time.monotonic() + GOOD_FOR)
    logger.info("pairing started for %r (%s, %d cores, %s)",
                machine.name, machine.os or "?", machine.cores, machine.build or "?")
    return code

def describe(code: str) -> Optional[Pairing]:
    _sweep()
    return _pending.get(invites.tidy(code))

def approve(code: str, telegram_id: int, name: str = "") -> Optional[Pairing]:
    _sweep()
    code = invites.tidy(code)
    found = _pending.get(code)
    if found is None or found.linked_to is not None:
        return None
    linked = found._replace(linked_to=telegram_id, linked_name=name)
    _pending[code] = linked
    logger.info("pairing of %r approved by %s", found.machine.name, telegram_id)
    return linked

def decline(code: str) -> bool:
    _sweep()
    gone = _pending.pop(invites.tidy(code), None)
    if gone is not None:
        logger.info("pairing of %r declined", gone.machine.name)
    return gone is not None

async def collect(code: str, address: str) -> Collected:
    _sweep()
    code = invites.tidy(code)
    found = _pending.get(code)
    if found is None:
        if not _within(_misses, address, MOST_MISSES, MISSES_WINDOW):
            logger.warning("too many unknown pairing codes from %s — refusing for now", address)
            return Collected(THROTTLED)
        return Collected(GONE)
    if found.linked_to is None:
        return Collected(WAITING)
    del _pending[code]
    invite = invites.Invite(found.linked_to, found.linked_name, found.expires_at)
    token = await invites.issue(invite, found.machine.name)
    return Collected(LINKED, token)

def pending() -> int:
    _sweep()
    return len(_pending)

def _sweep(*, now: Optional[float] = None) -> None:
    now = now if now is not None else time.monotonic()
    for code, pairing in list(_pending.items()):
        if pairing.expires_at <= now:
            del _pending[code]
    for book, window in ((_starts, STARTS_WINDOW), (_misses, MISSES_WINDOW)):
        for key, times in list(book.items()):
            if not times or now - times[-1] >= window:
                del book[key]

def _within(book: dict[str, list[float]], key: str, most: int, window: float,
            *, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.monotonic()
    recent = [at for at in book.get(key, ()) if now - at < window]
    if len(recent) >= most:
        book[key] = recent
        return False
    recent.append(now)
    book[key] = recent
    return True
