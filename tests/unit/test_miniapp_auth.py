"""Proving who opened the mini-app.

`initData` arrives from a browser, which is to say from anybody willing to type
a URL. Reading the user id out of it and believing it would let one person read
and rewrite another's settings by editing a query string — so every one of
these is about a way that could be attempted.

The valid blob is built here rather than pasted from a real session: a fixture
copied out of Telegram would carry somebody's actual account, and it would rot
the moment the freshness rule counted a day.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.miniapp.auth import (  # noqa: E402
    MAX_AGE_SECONDS,
    NotFromTelegram,
    check,
    who,
)

TOKEN = "123456:a-token-only-the-bot-has"


def signed(token: str = TOKEN, *, at: float | None = None, **over) -> str:
    """A blob Telegram would have produced, for a token of our choosing."""
    fields = {
        "auth_date": str(int(at if at is not None else time.time())),
        "query_id": "AAAAAAAA",
        "user": json.dumps({"id": 4242, "first_name": "Игрок"}, ensure_ascii=False),
    }
    fields.update({k: str(v) for k, v in over.items()})
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_a_blob_the_bot_token_signed_is_accepted():
    assert who(signed(), TOKEN) == 4242


def test_the_user_arrives_already_parsed():
    """Every caller wants the id and none of them want to parse the JSON."""
    fields = check(signed(), TOKEN)
    assert fields["user"]["first_name"] == "Игрок"


def test_somebody_elses_signature_is_refused():
    """The whole point. Another bot's token cannot sign into this one."""
    with pytest.raises(NotFromTelegram):
        who(signed("999:a-different-bot"), TOKEN)


def test_an_edited_field_breaks_the_signature():
    """The attack this exists for: change the id, keep the hash."""
    blob = signed()
    tampered = blob.replace("4242", "1")
    assert tampered != blob
    with pytest.raises(NotFromTelegram):
        who(tampered, TOKEN)


def test_an_extra_field_breaks_the_signature():
    """Adding a field is as much a forgery as changing one — the check string
    is built from every field there is, so a new one has to be signed too."""
    with pytest.raises(NotFromTelegram):
        who(signed() + "&is_admin=1", TOKEN)


def test_a_blob_with_no_signature_is_refused():
    with pytest.raises(NotFromTelegram):
        who("user=%7B%22id%22%3A1%7D&auth_date=99", TOKEN)


def test_nothing_at_all_is_refused():
    for nothing in ("", "   ", "?", "hash="):
        with pytest.raises(NotFromTelegram):
            who(nothing, TOKEN)


def test_a_deployment_with_no_token_refuses_everything():
    """It cannot check anything, so it must not wave anything through."""
    with pytest.raises(NotFromTelegram):
        who(signed(), "")


def test_a_signature_does_not_last_for_ever():
    """A blob copied out of a browser — a screenshot, a URL in an issue —
    would otherwise be a login with no expiry."""
    old = signed(at=time.time() - MAX_AGE_SECONDS - 60)
    with pytest.raises(NotFromTelegram):
        who(old, TOKEN)


def test_a_blob_from_yesterday_still_works_within_the_window():
    fresh = signed(at=time.time() - MAX_AGE_SECONDS + 600)
    assert who(fresh, TOKEN) == 4242


def test_the_age_cannot_be_edited_around():
    """`auth_date` is inside the signed data, which is the only reason a
    freshness rule is worth anything at all."""
    old = signed(at=time.time() - MAX_AGE_SECONDS - 60)
    with_today = old.replace(
        f"auth_date={int(time.time() - MAX_AGE_SECONDS - 60)}",
        f"auth_date={int(time.time())}",
    )
    with pytest.raises(NotFromTelegram):
        who(with_today, TOKEN)


def test_a_clock_a_few_seconds_ahead_is_not_an_attack():
    """Servers drift. Refusing everybody over three seconds would be worse than
    the thing being defended against."""
    assert who(signed(at=time.time() + 3), TOKEN) == 4242


def test_a_blob_from_next_year_is():
    with pytest.raises(NotFromTelegram):
        who(signed(at=time.time() + 400 * 24 * 3600), TOKEN)


def test_a_blob_naming_no_user_is_refused():
    """A signature proves the blob came from Telegram, not that it says who."""
    blob = urlencode({"auth_date": str(int(time.time())), "query_id": "x"})
    check_string = "\n".join(
        sorted(f"{k}={v}" for k, v in
               [("auth_date", str(int(time.time()))), ("query_id", "x")])
    )
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(NotFromTelegram):
        who(f"{blob}&hash={signature}", TOKEN)


def test_every_way_of_failing_is_the_same_kind_of_failure():
    """So an endpoint cannot accidentally answer a bad signature differently
    from a stale one. The difference between those two is exactly what somebody
    poking at it would like to learn, and a `except StaleData` next to a
    `except BadSignature` is how it would leak without anybody deciding to.

    The messages do differ — those are for the log, and a log saying "refused"
    is worth nothing.
    """
    said = []
    for blob in (signed("999:another"), signed() + "&x=1",
                 signed(at=time.time() - MAX_AGE_SECONDS - 60), "", "hash=x"):
        with pytest.raises(NotFromTelegram) as refused:
            who(blob, TOKEN)
        said.append(str(refused.value))
    assert type(refused.value) is NotFromTelegram
    assert len(set(said)) > 1, "the log cannot tell these apart either"
