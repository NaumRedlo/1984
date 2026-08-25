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
    # The page draws a control it has never heard of from this, and holds no
    # name, label or bound of its own.
    assert set(body["fields"]) == set(body["settings"])
    assert body["fields"]["mute"]["kind"] == "switch"
    assert body["fields"]["meter"] == {
        "kind": "number", "label": "Шкала точности",
        "hint": "От 50 до 300 процентов.", "low": 50, "high": 300,
    }
    assert body["fields"]["size"]["kind"] == "text", "a size is not a slider"

    # And lays them out in the groups the bot's own tabs use.
    grouped = {k for group in body["groups"] for k in group["keys"]}
    assert grouped == set(body["settings"]), "a setting belongs to no group"
    assert body["groups"][0]["label"] == "Рендер"


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


async def test_what_a_person_is_shown_is_in_their_own_language(app):
    """The page shows the server's words directly, so English machine text
    would land in front of a Russian reader. It is the sentence the typed
    prompts already use, with the same hint after it — somebody who has met
    this in the bot meets the same words here."""
    client, _ = app
    reply = await client.post("/app/api/settings", headers=auth(), json={"meter": 30})
    said = await reply.json()

    assert said["code"] == "out-of-range", "the page still needs a code to act on"
    assert said["error"] == "Такое значение не подходит. От 50 до 300 процентов."
    assert said["key"] == "meter", "and which row to point at"


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


# ── the skins, as pictures ───────────────────────────────────────────────────
#
# A column of buttons with names on them is the worst possible way to choose
# between things whose entire point is how they look.


@pytest_asyncio.fixture
async def store(monkeypatch, tmp_path):
    """A skin store of this test's own, and previews that pretend to exist."""
    from services.dossier import preview
    from services.dossier import skins as skin_store

    folders = {"mine-one": True, "theirs-one": True, "no-circle": False}
    for name in folders:
        (tmp_path / name).mkdir()

    monkeypatch.setattr(api.skin_store, "by_owner",
                        lambda _tg: (["mine-one"], ["theirs-one", "no-circle"]))
    monkeypatch.setattr(api.skin_store, "stale", lambda: ["theirs-one"])
    monkeypatch.setattr(
        api.skin_store, "folder_of",
        lambda name: str(tmp_path / name) if name in folders else None,
    )
    # A skin with no hit circle has no preview, which is a case the grid draws
    # differently and so is a case worth having.
    monkeypatch.setattr(
        api.preview, "path_of",
        lambda name, **_kw: str(tmp_path / f"{name}.png") if folders.get(name) else None,
    )
    for name, has in folders.items():
        if has:
            (tmp_path / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    del preview, skin_store
    yield tmp_path


async def test_the_skins_come_split_the_way_the_bots_own_picker_splits_them(app, store):
    client, _ = app
    said = await (await client.get("/app/api/skins", headers=auth())).json()

    assert [s["name"] for s in said["mine"]] == ["mine-one"]
    # The engine's own look leads the shared list rather than getting a heading
    # to itself: it belongs to nobody, which is what shared means.
    assert [s["name"] for s in said["shared"]] == ["classic", "theirs-one", "no-circle"]
    assert said["current"] == "classic"


async def test_a_skin_with_nothing_to_show_says_so_rather_than_borrowing_a_picture(
    app, store
):
    client, _ = app
    said = await (await client.get("/app/api/skins", headers=auth())).json()
    by_name = {s["name"]: s for s in said["shared"]}

    assert by_name["theirs-one"]["preview"] == "/app/preview/theirs-one.png"
    assert by_name["no-circle"]["preview"] is None


async def test_a_skin_unpacked_by_older_code_is_marked(app, store):
    """The store keeps no archive to redo it from, so the only way back is
    somebody sending it again — which marking is what lets them be asked."""
    client, _ = app
    said = await (await client.get("/app/api/skins", headers=auth())).json()
    by_name = {s["name"]: s for s in said["shared"]}
    assert by_name["theirs-one"]["stale"] and not by_name["no-circle"]["stale"]


async def test_choosing_a_skin_stores_it(app, store):
    client, held = app
    reply = await client.post("/app/api/skin", headers=auth(), json={"name": "mine-one"})
    assert reply.status == 200
    assert held["choices"].skin == "mine-one"


async def test_choosing_the_engines_own_look_stores_no_skin_at_all(app, store):
    """`classic` is the absence of a skin rather than one of them, and the row
    keeps `None` — which is what every other reader of `Choices` expects."""
    client, held = app
    held["choices"].skin = "mine-one"
    await client.post("/app/api/skin", headers=auth(), json={"name": "classic"})
    assert held["choices"].skin is None


async def test_a_skin_that_is_not_in_the_store_is_refused(app, store):
    """The store is the authority, not the page: a grid outlives the skin it
    was drawn for, exactly as a keyboard does."""
    client, held = app
    for tried in ("gone", "../../etc/passwd", "", None):
        reply = await client.post("/app/api/skin", headers=auth(), json={"name": tried})
        assert reply.status == 404, tried
    assert held["choices"].skin is None


async def test_choosing_a_skin_needs_a_signature(app, store):
    client, held = app
    reply = await client.post("/app/api/skin", json={"name": "mine-one"})
    assert reply.status == 401
    assert held["choices"].skin is None


# ── the previews, which are the one thing served to anybody ─────────────────


async def test_a_preview_is_served_without_a_signature(app, store):
    """A grid loads these with `<img src>`, which cannot carry an
    Authorization header. What a request can learn is that this host has a
    skin by that name, which anybody who can use the bot already knows."""
    client, _ = app
    reply = await client.get("/app/preview/mine-one.png")
    assert reply.status == 200
    assert reply.headers["Content-Type"] == "image/png"


async def test_a_name_that_is_not_a_skin_reaches_no_file(app, store):
    """The name still goes through the store's own listing, so anything that
    is not a skin is not a file."""
    client, _ = app
    for tried in ("../../etc/passwd", "no-circle", "nothing"):
        assert (await client.get(f"/app/preview/{tried}.png")).status == 404, tried


async def test_the_farm_says_why_in_the_readers_language(app, monkeypatch):
    """A worker writes its reason in English and somebody reads the app in
    Russian. The word it also sends is what gets translated."""
    from services.render_farm.roster import Roster

    roster = Roster()
    roster.hello("laptop", build=None, capacity={
        "take": False, "reason": "on battery at 12%",
        "code": "battery", "detail": "12", "threads": 0,
    })
    monkeypatch.setattr(api, "render_roster", roster)

    client, _ = app
    said = await (await client.get("/app/api/farm", headers=auth())).json()
    assert said["workers"][0]["reason"] == "на батарее, заряд 12%"


async def test_a_worker_too_old_to_send_a_word_still_says_something(app, monkeypatch):
    """Something in the wrong language beats a blank where an explanation
    should be."""
    from services.render_farm.roster import Roster

    roster = Roster()
    roster.hello("laptop", capacity={"take": False, "reason": "on battery at 12%"})
    monkeypatch.setattr(api, "render_roster", roster)

    client, _ = app
    said = await (await client.get("/app/api/farm", headers=auth())).json()
    assert said["workers"][0]["reason"] == "on battery at 12%"


async def test_a_word_nobody_knows_falls_back_rather_than_showing_a_key(app, monkeypatch):
    from services.render_farm.roster import Roster

    roster = Roster()
    roster.hello("laptop", capacity={
        "take": False, "reason": "something new happened", "code": "invented",
    })
    monkeypatch.setattr(api, "render_roster", roster)

    client, _ = app
    said = await (await client.get("/app/api/farm", headers=auth())).json()
    assert said["workers"][0]["reason"] == "something new happened"
