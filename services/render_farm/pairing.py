import secrets
import time
from datetime import datetime, timedelta
from typing import NamedTuple, Optional

from sqlalchemy import delete, func, select, update

from db.models.bot_settings import BotSettings
from db.models.pairing import PairingRow
from services.render_farm import invites
from utils.logger import get_logger
from utils.timeutils import utcnow

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
    expires_at: datetime
    linked_to: Optional[int] = None
    linked_name: str = ""
    linked_player: Optional[int] = None
    wants_telegram_for: Optional[int] = None

class Collected(NamedTuple):
    status: str
    token: Optional[str] = None
    who: str = ""

BOT_USERNAME_KEY = "bot_username"

_starts: dict[str, list[float]] = {}
_misses: dict[str, list[float]] = {}

bot_username: str = ""

def _factory():
    from db.database import AsyncSessionFactory

    return AsyncSessionFactory()

def _now() -> datetime:
    return utcnow()

def _of(row: PairingRow) -> Pairing:
    return Pairing(Machine(row.name, row.os or "", row.cores or 0, row.build or ""), row.expires_at,
                   row.linked_to, row.linked_name or "", row.linked_player, row.wants_telegram_for)

def _code() -> str:
    return "".join(secrets.choice(invites.ALPHABET) for _ in range(invites.CODE_LENGTH))

def _open(code: str, now: datetime) -> list:
    return [PairingRow.code == code, PairingRow.expires_at > now]

def _unanswered(code: str, now: datetime) -> list:
    return _open(code, now) + [PairingRow.linked_to.is_(None), PairingRow.wants_telegram_for.is_(None)]

def set_bot_username(name: str) -> None:
    global bot_username
    bot_username = (name or "").lstrip("@")

async def publish_bot_username(name: str) -> None:
    set_bot_username(name)
    async with _factory() as session:
        kept = await session.get(BotSettings, BOT_USERNAME_KEY)
        if kept is None:
            session.add(BotSettings(key=BOT_USERNAME_KEY, value=bot_username))
        else:
            kept.value = bot_username
        await session.commit()

async def _bot_username() -> str:
    if bot_username:
        return bot_username
    try:
        async with _factory() as session:
            kept = await session.get(BotSettings, BOT_USERNAME_KEY)
    except Exception as exc:
        logger.warning("the bot's username could not be read: %s", exc)
        return ""
    set_bot_username(kept.value if kept is not None and kept.value else "")
    return bot_username

async def link(code: str) -> str:
    name = await _bot_username()
    if not name:
        return ""
    return f"https://t.me/{name}?start=pair-{invites.tidy(code)}"

async def _begin(machine: Machine, wants_telegram_for: Optional[int] = None) -> Optional[str]:
    now = _now()
    code = _code()
    async with _factory() as session:
        await session.execute(delete(PairingRow).where(PairingRow.expires_at <= now))
        waiting = await session.scalar(select(func.count()).select_from(PairingRow)) or 0
        if waiting < MOST_PENDING:
            session.add(PairingRow(code=code, name=machine.name[:255], os=machine.os[:64], cores=machine.cores,
                                   build=machine.build[:64], expires_at=now + timedelta(seconds=GOOD_FOR),
                                   linked_name="", wants_telegram_for=wants_telegram_for))
        await session.commit()
    return code if waiting < MOST_PENDING else None

async def start(machine: Machine, address: str) -> Optional[str]:
    _sweep()
    if not _within(_starts, address, MOST_STARTS, STARTS_WINDOW):
        logger.warning("too many pairings started from %s — refusing for now", address)
        return None
    code = await _begin(machine)
    if code is None:
        logger.warning("too many pairings waiting — refusing for now")
        return None
    logger.info("pairing started for %r (%s, %d cores, %s)",
                machine.name, machine.os or "?", machine.cores, machine.build or "?")
    return code

async def describe(code: str) -> Optional[Pairing]:
    async with _factory() as session:
        row = await session.scalar(select(PairingRow).where(*_open(invites.tidy(code), _now())))
    return _of(row) if row is not None else None

async def approve(code: str, telegram_id: int, name: str = "", player_id: Optional[int] = None) -> Optional[Pairing]:
    code = invites.tidy(code)
    async with _factory() as session:
        changed = await session.execute(
            update(PairingRow).where(*_unanswered(code, _now()))
            .values(linked_to=telegram_id, linked_name=(name or "")[:255], linked_player=player_id)
        )
        if changed.rowcount != 1:
            await session.rollback()
            return None
        await session.commit()
        row = await session.scalar(select(PairingRow).where(PairingRow.code == code))
    if row is None:
        return None
    logger.info("pairing of %r approved by %s", row.name, telegram_id or f"player {player_id}")
    return _of(row)

async def osu_state(code: str) -> Optional[str]:
    async with _factory() as session:
        row = await session.scalar(select(PairingRow).where(*_unanswered(invites.tidy(code), _now())))
        if row is None:
            return None
        if not row.osu_state:
            row.osu_state = "app." + secrets.token_urlsafe(32)
            await session.commit()
        return row.osu_state

async def take_osu_state(state: str) -> Optional[str]:
    if not state:
        return None
    async with _factory() as session:
        code = await session.scalar(select(PairingRow.code).where(PairingRow.osu_state == state,
                                                                  PairingRow.expires_at > _now()))
        if code is None:
            return None
        taken = await session.execute(
            update(PairingRow).where(PairingRow.code == code, PairingRow.osu_state == state).values(osu_state=None)
        )
        await session.commit()
    return code if taken.rowcount == 1 else None

async def ask_telegram(player_id: int, name: str, address: str) -> Optional[str]:
    _sweep()
    if not _within(_starts, address, MOST_STARTS, STARTS_WINDOW):
        return None
    return await _begin(Machine(name), wants_telegram_for=player_id)

async def _take(code: str, *more) -> Optional[Pairing]:
    async with _factory() as session:
        row = await session.scalar(select(PairingRow).where(*_open(code, _now()), *more))
        if row is None:
            return None
        gone = await session.execute(delete(PairingRow).where(PairingRow.code == code, *more))
        await session.commit()
    return _of(row) if gone.rowcount == 1 else None

async def answered(code: str) -> Optional[Pairing]:
    return await _take(invites.tidy(code))

async def decline(code: str) -> bool:
    gone = await _take(invites.tidy(code))
    if gone is not None:
        logger.info("pairing of %r declined", gone.machine.name)
    return gone is not None

async def collect(code: str, address: str) -> Collected:
    _sweep()
    code = invites.tidy(code)
    found = await describe(code)
    if found is None:
        if not _within(_misses, address, MOST_MISSES, MISSES_WINDOW):
            logger.warning("too many unknown pairing codes from %s — refusing for now", address)
            return Collected(THROTTLED)
        return Collected(GONE)
    if found.wants_telegram_for is not None:
        return Collected(GONE)
    if found.linked_to is None:
        return Collected(WAITING)
    found = await _take(code, PairingRow.linked_to.is_not(None))
    if found is None:
        return Collected(GONE)
    invite = invites.Invite(found.linked_to, found.linked_name, 0.0, found.linked_player)
    token = await invites.issue(invite, found.machine.name)
    return Collected(LINKED, token, found.linked_name)

async def pending() -> int:
    async with _factory() as session:
        return await session.scalar(select(func.count()).select_from(PairingRow).where(PairingRow.expires_at > _now())) or 0

def _sweep(*, now: Optional[float] = None) -> None:
    now = now if now is not None else time.monotonic()
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
