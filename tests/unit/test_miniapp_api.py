"""The settings, over HTTP instead of a column of buttons.

Everything the API decides for itself is here. What it does *not* decide —
ranges, the 4K ration, who may render at all — is reused from the bot, so the
tests for those live where those live; what is tested here is that the reuse
actually happens, since an endpoint that quietly grew its own copy of a rule is
exactly how the two sides drift.
"""

import json
import os
import sys
import time

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bot.handlers.dossier import renders  # noqa: E402
from services.miniapp import api  # noqa: E402
from tests.unit.test_miniapp_auth import TOKEN, signed  # noqa: E402


@pytest_asyncio.fixture
async def app(monkeypatch):
    """The endpoints, over a real listener, with the bot's own side faked.

    `_load` and `_store` are the bot's and are tested there. Standing a
    database up here would test SQLAlchemy rather than this file.
    """
    held = {"choices": renders.Choices()}

    async def load(_tg, _tenant):
        return held["choices"]

    async def store(_tg, _tenant, choices):
        held["choices"] = choices

    async def tenant(_tg):
        return -1001

    async def no_refusal(*_a, **_kw):
        return None

    async def language(_tg):
        return "ru"

    monkeypatch.setattr(api, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(api, "_load", load)
    monkeypatch.setattr(api, "_store", store)
    monkeypatch.setattr(api, "_tenant", tenant)
    monkeypatch.setattr(api, "rationed", no_refusal)

    async def five_left(_tg, _tenant):
        return 5

    monkeypatch.setattr(api, "_ration", five_left)
    monkeypatch.setattr(api, "get_language", language)
    monkeypatch.setattr(api, "can_use_render", lambda _tg: True)

    application = web.Application()
    assert api.install(application)
    client = TestClient(TestServer(application))
    await client.start_server()
    yield client, held
    await client.close()


def auth(blob: str | None = None) -> dict:
    return {"Authorization": f"tma {blob if blob is not None else signed()}"}


# ── who is asking ────────────────────────────────────────────────────────────


async def test_a_request_with_no_signature_is_refused(app):
    client, _ = app
    assert (await client.get("/app/api/settings")).status == 401


async def test_a_request_signed_by_another_bot_is_refused(app):
    client, _ = app
    reply = await client.get("/app/api/settings", headers=auth(signed("999:other")))
    assert reply.status == 401


async def test_a_refusal_does_not_say_which_check_failed(app):
    """Whether the signature was wrong or merely stale is the one thing worth
    learning from outside, so neither is said."""
    client, _ = app
    stale = signed(at=time.time() - 10 * 24 * 3600)
    wrong = signed("999:other")
    said = set()
    for blob in (stale, wrong):
        reply = await client.get("/app/api/settings", headers=auth(blob))
        said.add(await reply.text())
    assert len(said) == 1, f"the two refusals differ: {said}"


async def test_somebody_without_render_access_is_told_so(app, monkeypatch):
    client, _ = app
    monkeypatch.setattr(api, "can_use_render", lambda _tg: False)
    assert (await client.get("/app/api/settings", headers=auth())).status == 403


# ── reading ──────────────────────────────────────────────────────────────────


async def test_reading_gives_the_settings_and_how_to_draw_them(app):
    client, _ = app
    body = await (await client.get("/app/api/settings", headers=auth())).json()

    assert body["settings"]["fps"] == 60
    assert body["heavy_left"] == 5, "the page cannot draw the 4K control without it"
    assert "mute" in body["switches"] and "fps" in body["typed"]
    assert set(body["settings"]) == set(body["switches"]) | set(body["typed"])


# ── writing ──────────────────────────────────────────────────────────────────


async def test_a_change_is_stored(app):
    client, held = app
    reply = await client.post(
        "/app/api/settings", headers=auth(), json={"fps": 30, "mute": True}
    )
    assert reply.status == 200
    assert held["choices"].fps == 30 and held["choices"].mute is True
    assert (await reply.json())["settings"]["fps"] == 30


async def test_a_key_nobody_knows_is_named_and_refused(app):
    """A page that quietly drops what it does not recognise is a page that
    looks like it saved and did not."""
    client, held = app
    reply = await client.post(
        "/app/api/settings", headers=auth(), json={"fps": 30, "colour": "red"}
    )
    assert reply.status == 400
    assert (await reply.json())["keys"] == ["colour"]
    assert held["choices"].fps == 60, "a refused write changed something anyway"


async def test_a_field_the_engine_needs_is_not_web_writable(app):
    """`skin` is a name that has to exist in somebody's own store. A text box
    could write one nobody has."""
    client, _ = app
    reply = await client.post(
        "/app/api/settings", headers=auth(), json={"skin": "/etc/passwd"}
    )
    assert reply.status == 400


async def test_a_value_out_of_range_is_refused_by_the_bots_own_parser(app):
    """The parsers come from the typed prompts, which another test holds to the
    engine's own ranges. This one checks the reuse happens at all."""
    client, held = app
    for key, bad in (("fps", 9000), ("meter", 30), ("size", "1601x900")):
        reply = await client.post("/app/api/settings", headers=auth(), json={key: bad})
        assert reply.status == 400, f"{key}={bad} was accepted"
        assert (await reply.json())["key"] == key
    assert held["choices"] == renders.Choices(), "a refused write leaked through"


async def test_a_switch_wants_a_yes_or_no(app):
    client, _ = app
    reply = await client.post("/app/api/settings", headers=auth(), json={"mute": "yes"})
    assert reply.status == 400


async def test_as_it_comes_is_a_real_choice(app):
    """`None` is the engine's own default, which is what a fresh account has and
    a page has to be able to go back to."""
    client, held = app
    held["choices"].dim = 80
    reply = await client.post("/app/api/settings", headers=auth(), json={"dim": None})
    assert reply.status == 200
    assert held["choices"].dim is None


async def test_the_ration_is_asked_before_a_big_size_is_stored(app, monkeypatch):
    """The third way into the same rule. One with a way round it is not a rule."""
    asked = []

    async def refuse(tg, tenant, before, after, lang):
        asked.append(after.size)
        return "нет квоты на сегодня"

    monkeypatch.setattr(api, "rationed", refuse)
    client, held = app
    reply = await client.post(
        "/app/api/settings", headers=auth(), json={"size": "3840x2160"}
    )
    assert reply.status == 409
    assert asked == ["3840x2160"]
    assert held["choices"].size == "1280x720", "the ration was asked and ignored"


async def test_an_empty_body_changes_nothing(app):
    client, _ = app
    assert (await client.post("/app/api/settings", headers=auth(), json={})).status == 400


async def test_a_body_that_is_not_an_object_is_refused(app):
    client, _ = app
    reply = await client.post(
        "/app/api/settings",
        headers={**auth(), "Content-Type": "application/json"},
        data=json.dumps(["fps", 30]),
    )
    assert reply.status == 400
