"""Who opened the page, proved rather than claimed.

A mini-app is an ordinary web page in a Telegram web view. It is handed a blob
called `initData` naming the user who opened it — and that blob arrives from a
browser, which means it arrives from whoever is willing to type a URL. Taking
the id out of it and believing it would let anybody read and rewrite anybody
else's settings by editing a query string.

So it is signed, and this checks the signature. The scheme is Telegram's:

    data_check_string = every field except `hash`, as `key=value`,
                        sorted by key, joined with newlines
    secret            = HMAC-SHA256("WebAppData", bot_token)
    expected          = HMAC-SHA256(secret, data_check_string)

Only somebody holding the bot token can produce that, and the bot token is the
thing the bot already keeps secret.

## Why the age is checked too

A signature stays valid for ever. `initData` copied out of somebody's browser —
a screenshot in a support chat, a URL pasted into an issue — would otherwise be
a login that never expires. `auth_date` is inside the signed data, so a
freshness rule cannot be forged around; a day is long enough that nobody is
logged out mid-session and short enough that a leaked blob stops working.

## What this deliberately does not do

It does not decide what somebody may *do*. It answers "who is this" and
nothing else, so every permission stays where it already lives — the render
gate, the tenant lookup, the ration.
"""

import hashlib
import hmac
import json
import time
from typing import Any, Optional
from urllib.parse import parse_qsl

from utils.logger import get_logger

logger = get_logger("services.miniapp.auth")

# How old a signed blob may be. A day: long enough that nobody is thrown out
# while they are using the thing, short enough that one copied out of a browser
# is not a permanent key to somebody's account.
MAX_AGE_SECONDS = 24 * 60 * 60

_SECRET_KEY = b"WebAppData"


class NotFromTelegram(Exception):
    """The blob did not come from Telegram, or is too old to trust.

    One exception type for every way of failing, deliberately: an endpoint
    cannot then accidentally answer a bad signature differently from a stale
    one, and the difference between those two is the one thing somebody poking
    at it would like to know.

    The *messages* do differ, because a log with "signed too long ago" in it is
    worth having and a log with "refused" in it is not. They are for the log.
    Whatever answers a request must send back its own words — see the endpoints
    in `services/miniapp`.
    """


def check(init_data: str, token: str, *, now: Optional[float] = None) -> dict[str, Any]:
    """The fields of `init_data`, if it was signed by the holder of `token`.

    Raises [`NotFromTelegram`] otherwise. The returned `user` is already parsed
    out of its JSON, since every caller wants the id and none of them want to
    do that twice.
    """
    if not token:
        # Not an authentication failure — a deployment that cannot check
        # anything must refuse everything rather than wave it through.
        raise NotFromTelegram("no bot token to check against")

    pairs = dict(parse_qsl(init_data or "", keep_blank_values=True))
    given = pairs.pop("hash", "")
    if not given or not pairs:
        raise NotFromTelegram("no signature")

    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(_SECRET_KEY, token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, given):
        raise NotFromTelegram("the signature does not match")

    # Inside the signed data, so this cannot be forged around — which is the
    # only reason a freshness rule is worth anything.
    try:
        signed_at = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise NotFromTelegram("no readable auth_date") from exc
    age = (now if now is not None else time.time()) - signed_at
    if signed_at <= 0 or age > MAX_AGE_SECONDS:
        raise NotFromTelegram("signed too long ago")
    # A blob from the future is a clock disagreeing, not an attack; a little
    # slack keeps a server whose clock drifts by seconds from refusing
    # everybody. More than that is not a clock.
    if age < -300:
        raise NotFromTelegram("signed in the future")

    fields: dict[str, Any] = dict(pairs)
    if "user" in fields:
        try:
            fields["user"] = json.loads(fields["user"])
        except (json.JSONDecodeError, ValueError) as exc:
            raise NotFromTelegram("the user field is not readable") from exc
    return fields


def who(init_data: str, token: str, *, now: Optional[float] = None) -> int:
    """The Telegram id of whoever opened the page. Raises if it cannot be told.

    The one thing nearly every handler wants, so it does not have to reach into
    the fields and know their shape.
    """
    user = check(init_data, token, now=now).get("user") or {}
    telegram_id = user.get("id")
    if not isinstance(telegram_id, int):
        raise NotFromTelegram("the blob names no user")
    return telegram_id


__all__ = ["check", "who", "NotFromTelegram", "MAX_AGE_SECONDS"]
