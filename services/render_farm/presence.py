from time import monotonic
from typing import Optional

from services.render_farm import invites

PRESENT_FOR = 180.0
PRESENCE_BEAT_FOR = 30.0

_seen: dict[str, tuple[float, float]] = {}


def forget() -> None:
    _seen.clear()


def touch(token: str, *, now: Optional[float] = None, keep: Optional[float] = None) -> None:
    key = invites.digest(token)
    if key in invites._good and key in invites._owners:
        span = keep if keep is not None else _seen.get(key, (0, PRESENT_FOR))[1]
        _seen[key] = (monotonic() if now is None else now, span)


def present(*, now: Optional[float] = None) -> list[invites.Owner]:
    now = monotonic() if now is None else now
    here = []
    for key, (at, span) in list(_seen.items()):
        if key not in invites._good or key not in invites._owners or now - at >= span:
            _seen.pop(key, None)
        else:
            here.append(invites._owners[key])
    return here


def online(*, now: Optional[float] = None) -> int:
    return len({owner.player_id if owner.player_id is not None else -owner.telegram_id for owner in present(now=now)})
