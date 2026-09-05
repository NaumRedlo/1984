import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.miniapp.auth import (
    MAX_AGE_SECONDS,
    NotFromTelegram,
    check,
    who,
)

TOKEN = "123456:a-token-only-the-bot-has"

def signed(token: str = TOKEN, *, at: float | None = None, **over) -> str:
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
    fields = check(signed(), TOKEN)
    assert fields["user"]["first_name"] == "Игрок"

def test_somebody_elses_signature_is_refused():
    with pytest.raises(NotFromTelegram):
        who(signed("999:a-different-bot"), TOKEN)

def test_an_edited_field_breaks_the_signature():
    blob = signed()
    tampered = blob.replace("4242", "1")
    assert tampered != blob
    with pytest.raises(NotFromTelegram):
        who(tampered, TOKEN)

def test_an_extra_field_breaks_the_signature():
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
    with pytest.raises(NotFromTelegram):
        who(signed(), "")

def test_a_signature_does_not_last_for_ever():
    old = signed(at=time.time() - MAX_AGE_SECONDS - 60)
    with pytest.raises(NotFromTelegram):
        who(old, TOKEN)

def test_a_blob_from_yesterday_still_works_within_the_window():
    fresh = signed(at=time.time() - MAX_AGE_SECONDS + 600)
    assert who(fresh, TOKEN) == 4242

def test_the_age_cannot_be_edited_around():
    old = signed(at=time.time() - MAX_AGE_SECONDS - 60)
    with_today = old.replace(
        f"auth_date={int(time.time() - MAX_AGE_SECONDS - 60)}",
        f"auth_date={int(time.time())}",
    )
    with pytest.raises(NotFromTelegram):
        who(with_today, TOKEN)

def test_a_clock_a_few_seconds_ahead_is_not_an_attack():
    assert who(signed(at=time.time() + 3), TOKEN) == 4242

def test_a_blob_from_next_year_is():
    with pytest.raises(NotFromTelegram):
        who(signed(at=time.time() + 400 * 24 * 3600), TOKEN)

def test_a_blob_naming_no_user_is_refused():
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
    said = []
    for blob in (signed("999:another"), signed() + "&x=1",
                 signed(at=time.time() - MAX_AGE_SECONDS - 60), "", "hash=x"):
        with pytest.raises(NotFromTelegram) as refused:
            who(blob, TOKEN)
        said.append(str(refused.value))
    assert type(refused.value) is NotFromTelegram
    assert len(set(said)) > 1, "the log cannot tell these apart either"
