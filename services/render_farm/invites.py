import asyncio
import hashlib
import secrets
from typing import NamedTuple, Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.invites")

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8

GOOD_FOR = 600.0
RELOAD_EVERY = 600.0
CHANGED = "render_tokens"

class Invite(NamedTuple):

    telegram_id: int
    name: str
    expires_at: float
    player_id: Optional[int] = None

class Owner(NamedTuple):

    telegram_id: int
    name: str
    player_id: Optional[int] = None

_good: set[str] = set()
_owners: dict[str, Owner] = {}

def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def tidy(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch in ALPHABET)

def pretty(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"

def remember(token: str, owner: Optional[Owner] = None) -> None:
    _good.add(digest(token))
    if owner is not None:
        _owners[digest(token)] = owner

def forget(token_digest: str) -> None:
    _good.discard(token_digest)
    _owners.pop(token_digest, None)

def known(token: str) -> bool:
    return digest(token) in _good

def owner(token: str) -> Optional[Owner]:
    return _owners.get(digest(token))

def linked() -> set[int]:
    return {owner.telegram_id for owner in _owners.values() if owner.telegram_id}

def linked_players() -> set[int]:
    return {owner.player_id for owner in _owners.values() if owner.player_id is not None}

def loaded() -> int:
    return len(_good)

async def load() -> int:
    try:
        from sqlalchemy import select

        from db.database import AsyncSessionFactory
        from db.models import RenderWorkerToken

        async with AsyncSessionFactory() as session:
            rows = await session.execute(
                select(RenderWorkerToken.digest, RenderWorkerToken.issued_to,
                       RenderWorkerToken.issued_name, RenderWorkerToken.player_id).where(
                    RenderWorkerToken.revoked_at.is_(None)
                )
            )
            good, owners = set(), {}
            for row in rows.all():
                good.add(row[0])
                if row[1] is not None or row[3] is not None:
                    owners[row[0]] = Owner(int(row[1] or 0), row[2] or "", row[3])
            changed = good != _good or owners != _owners
            _good.clear()
            _good.update(good)
            _owners.clear()
            _owners.update(owners)
    except Exception as exc:
        logger.warning("could not read the enrolled machines: %s", exc)
        return 0
    if changed:
        logger.info("render farm: %d machine(s) enrolled", len(_good))
    return len(_good)

async def follow(shutdown: asyncio.Event) -> None:
    from utils.signals import shared

    shared.listen(CHANGED)
    stopping = asyncio.create_task(shutdown.wait())
    try:
        mark = shared.mark(CHANGED)
        while not shutdown.is_set():
            changed = asyncio.create_task(mark.wait())
            await asyncio.wait({stopping, changed}, timeout=RELOAD_EVERY, return_when=asyncio.FIRST_COMPLETED)
            changed.cancel()
            if not shutdown.is_set():
                mark = shared.mark(CHANGED)
                await load()
    finally:
        stopping.cancel()

async def issue(invite: Invite, worker: str = "") -> str:
    from db.database import AsyncSessionFactory
    from db.models import RenderWorkerToken
    from utils.signals import shared

    token = secrets.token_hex(32)
    async with AsyncSessionFactory() as session:
        session.add(RenderWorkerToken(
            digest=digest(token),
            issued_to=invite.telegram_id or None,
            player_id=invite.player_id,
            issued_name=invite.name or None,
            worker=worker or None,
        ))
        await shared.announce(session, CHANGED)
        await session.commit()
    remember(token, Owner(invite.telegram_id or 0, invite.name or "", invite.player_id))
    shared.heard(CHANGED)
    logger.info("machine %r enrolled for %s", worker or "?", invite.telegram_id or f"player {invite.player_id}")
    return token
