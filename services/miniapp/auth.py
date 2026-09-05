import hashlib
import hmac
import json
import time
from typing import Any, Optional
from urllib.parse import parse_qsl

from utils.logger import get_logger

logger = get_logger("services.miniapp.auth")

MAX_AGE_SECONDS = 24 * 60 * 60

_SECRET_KEY = b"WebAppData"

class NotFromTelegram(Exception):
    pass

def check(init_data: str, token: str, *, now: Optional[float] = None) -> dict[str, Any]:
    if not token:

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

    try:
        signed_at = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise NotFromTelegram("no readable auth_date") from exc
    age = (now if now is not None else time.time()) - signed_at
    if signed_at <= 0 or age > MAX_AGE_SECONDS:
        raise NotFromTelegram("signed too long ago")

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
    user = check(init_data, token, now=now).get("user") or {}
    telegram_id = user.get("id")
    if not isinstance(telegram_id, int):
        raise NotFromTelegram("the blob names no user")
    return telegram_id

__all__ = ["check", "who", "NotFromTelegram", "MAX_AGE_SECONDS"]
