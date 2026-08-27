"""Letting somebody's machine onto the farm without anybody handling a secret.

There was one token for everybody, copied out of a chat by hand. Two things
were wrong with that and both happened:

**A sixty-four character string copied slightly wrong looks exactly like one
copied right.** Two people ran for days against a token that differed from the
server's, and the only symptom was work that never arrived. We answered that
with fingerprints and a check at setup, which made it findable — but the step
that could go wrong was still there.

**Everybody held the same string.** Removing one person meant changing it for
all of them, and then sending the new one to five people, by hand, again.

So: the bot hands out a short code, good for ten minutes and for one machine.
The program swaps it for a token of its own and remembers that. Nobody ever
sees the long string — not the person setting a worker up, and not the person
who invited them.

The old shared token keeps working. Machines set up before this go on running,
and nobody is made to re-enrol by a deploy.

## Where things are kept

The durable record is a row per machine, holding `sha256` of the token and
never the token — a copy of that table must not be a working key for the farm.
But a worker asks this bot something roughly once a second, and a database
round trip per poll to compare a hash is a round trip spent on nothing. So the
digests live in a set in memory, filled from the table at startup, and the
table is what survives a restart.
"""

import hashlib
import secrets
import time
from typing import NamedTuple, Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.invites")

# No I, L, O, U: the first three because they are read as 1 and 0 by anybody
# typing from a phone screen, and the last because of what it makes of some
# eight-letter combinations.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8

# Long enough to walk to the other computer, short enough that a code left in a
# chat is worthless by the time anybody scrolls back to it.
GOOD_FOR = 600.0

# A code is thirty to the eighth — six hundred billion — and lives ten minutes,
# so guessing one is not a plan. This is here anyway, because a rate that makes
# guessing impossible also makes a script hammering the endpoint visible.
MOST_TRIES = 20
TRIES_WINDOW = 60.0


class Invite(NamedTuple):
    """A code that has been handed out and not yet used."""

    telegram_id: int
    name: str
    expires_at: float


# Deliberately not in the database. A code is worth ten minutes; a restart in
# that window costs somebody one `cltoken`, and the alternative is a table that
# has to be swept.
_codes: dict[str, Invite] = {}
_tries: list[float] = []

# Every token this bot will accept, as digests. Filled from the table at
# startup by `load`, added to as codes are redeemed.
_good: set[str] = set()


def digest(token: str) -> str:
    """What is stored and compared. Never the token itself.

    Plain `sha256` with no salt and no stretching, which is right here and
    would be wrong for a password: this is sixty-four bits of randomness we
    generated ourselves, so there is no guessing it and nothing to slow down.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def tidy(code: str) -> str:
    """What somebody typed, as the code they were given.

    People type in lower case, and they type the dash that the bot puts in to
    make eight characters readable. Neither is a wrong code.
    """
    return "".join(ch for ch in code.upper() if ch in ALPHABET)


def offer(telegram_id: int, name: str = "") -> str:
    """Make a code for somebody. Shown to them, and to nobody else."""
    _sweep()
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    _codes[code] = Invite(
        telegram_id=telegram_id, name=name, expires_at=time.monotonic() + GOOD_FOR
    )
    logger.info("invite offered to %s", telegram_id)
    return code


def pretty(code: str) -> str:
    """`ABCD-EFGH`. Four and four is what people can hold in their head."""
    return f"{code[:4]}-{code[4:]}"


def redeem(code: str) -> Optional[Invite]:
    """Spend a code. `None` if it was wrong, used already, or too old.

    One use: a code that stays good is a code somebody can scroll back to.
    """
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


# ── the tokens themselves ───────────────────────────────────────────────────


def remember(token: str) -> None:
    """Take a token as valid from now on, without waiting for a restart."""
    _good.add(digest(token))


def forget(token_digest: str) -> None:
    _good.discard(token_digest)


def known(token: str) -> bool:
    """Whether this bot has ever issued this token and still honours it."""
    return digest(token) in _good


def loaded() -> int:
    """How many machines are enrolled. For the log line at startup."""
    return len(_good)


async def load() -> int:
    """Fill the set from the table. Called once, when the bot starts.

    A bot that cannot reach its database has bigger problems than the farm, so
    a failure here is logged and left: the shared token still works, and every
    enrolled machine is refused until the next start rather than the bot
    refusing to start at all.
    """
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
    except Exception as exc:  # noqa: BLE001 — the farm is not worth a dead bot
        logger.warning("could not read the enrolled machines: %s", exc)
        return 0
    logger.info("render farm: %d machine(s) enrolled", len(_good))
    return len(_good)


async def issue(invite: Invite, worker: str = "") -> str:
    """Make a token for a redeemed code, and record the machine it is for.

    The token is returned once and never stored, so this is the only moment it
    exists anywhere but on the machine that asked for it.
    """
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
