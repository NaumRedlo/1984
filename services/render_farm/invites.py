import hashlib
import secrets
from time import monotonic
from typing import NamedTuple, Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.invites")

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8

GOOD_FOR = 600.0
PRESENT_FOR = 180.0
PRESENCE_BEAT_FOR = 30.0

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
_seen: dict[str, tuple[float, float]] = {}

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
    _seen.pop(token_digest, None)

def touch(token: str, *, now: Optional[float] = None, keep: Optional[float] = None) -> None:
    key = digest(token)
    if key in _good and key in _owners:
        span = keep if keep is not None else _seen.get(key, (0, PRESENT_FOR))[1]
        _seen[key] = (monotonic() if now is None else now, span)

def present(*, now: Optional[float] = None) -> list[Owner]:
    now = monotonic() if now is None else now
    here = []
    for key, (at, span) in list(_seen.items()):
        if key not in _good or key not in _owners or now - at >= span:
            _seen.pop(key, None)
        else:
            here.append(_owners[key])
    return here

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
            _good.clear()
            _owners.clear()
            _seen.clear()
            for row in rows.all():
                _good.add(row[0])
                if row[1] is not None or row[3] is not None:
                    _owners[row[0]] = Owner(int(row[1] or 0), row[2] or "", row[3])
    except Exception as exc:
        logger.warning("could not read the enrolled machines: %s", exc)
        return 0
    logger.info("render farm: %d machine(s) enrolled", len(_good))
    return len(_good)

async def issue(invite: Invite, worker: str = "") -> str:
    from db.database import AsyncSessionFactory
    from db.models import RenderWorkerToken

    token = secrets.token_hex(32)
    async with AsyncSessionFactory() as session:
        session.add(RenderWorkerToken(
            digest=digest(token),
            issued_to=invite.telegram_id or None,
            player_id=invite.player_id,
            issued_name=invite.name or None,
            worker=worker or None,
        ))
        await session.commit()
    remember(token, Owner(invite.telegram_id or 0, invite.name or "", invite.player_id))
    logger.info("machine %r enrolled for %s", worker or "?", invite.telegram_id or f"player {invite.player_id}")
    return token
