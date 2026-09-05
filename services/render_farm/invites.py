import hashlib
import secrets
import time
from typing import NamedTuple, Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.invites")

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8

GOOD_FOR = 600.0

MOST_TRIES = 20
TRIES_WINDOW = 60.0

class Invite(NamedTuple):

    telegram_id: int
    name: str
    expires_at: float

_codes: dict[str, Invite] = {}
_tries: list[float] = []

_good: set[str] = set()

def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def tidy(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch in ALPHABET)

def offer(telegram_id: int, name: str = "") -> str:
    _sweep()
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    _codes[code] = Invite(
        telegram_id=telegram_id, name=name, expires_at=time.monotonic() + GOOD_FOR
    )
    logger.info("invite offered to %s", telegram_id)
    return code

def pretty(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"

def redeem(code: str) -> Optional[Invite]:
    _sweep()
    if not _allowed_another_try():
        logger.warning("too many code attempts — refusing for now")
        return None
    return _codes.pop(tidy(code), None)

def _sweep(*, now: Optional[float] = None) -> None:
    now = now if now is not None else time.monotonic()
    for code, invite in list(_codes.items()):
        if invite.expires_at <= now:
            del _codes[code]

def _allowed_another_try(*, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.monotonic()
    _tries[:] = [at for at in _tries if now - at < TRIES_WINDOW]
    if len(_tries) >= MOST_TRIES:
        return False
    _tries.append(now)
    return True

def remember(token: str) -> None:
    _good.add(digest(token))

def forget(token_digest: str) -> None:
    _good.discard(token_digest)

def known(token: str) -> bool:
    return digest(token) in _good

def loaded() -> int:
    return len(_good)

async def load() -> int:
    try:
        from sqlalchemy import select

        from db.database import AsyncSessionFactory
        from db.models import RenderWorkerToken

        async with AsyncSessionFactory() as session:
            rows = await session.execute(
                select(RenderWorkerToken.digest).where(
                    RenderWorkerToken.revoked_at.is_(None)
                )
            )
            _good.clear()
            _good.update(row[0] for row in rows.all())
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
            issued_to=invite.telegram_id,
            issued_name=invite.name or None,
            worker=worker or None,
        ))
        await session.commit()
    remember(token)
    logger.info("machine %r enrolled for %s", worker or "?", invite.telegram_id)
    return token
