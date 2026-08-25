"""The mini-app's settings, read and written over HTTP.

The same settings the `sts` screen shows, reached from a web page instead of a
column of buttons. Fifteen values across four tabs of two-line buttons is the
part of this bot a phone is worst at, and a page is the shape it wanted.

## Nothing here decides anything for itself

Every rule already exists somewhere and this reuses it rather than restating
it. The values are parsed by `settings_menu.typed.FIELDS` — the same parsers
the typed prompts use, which a test already holds to the engine's own ranges.
The 4K ration is `settings_menu.render.rationed`. Access is `can_use_render`.
Reading and writing are `_load` and `_store`.

A second statement of any of those would be a second thing to keep in step, and
this project has already paid for that twice — once when a render option had to
be written out in eight places, and once when the bot offered a meter size the
engine refuses.

## The identity is never in the request

`services.miniapp.auth` takes it out of a signature only the bot could have
produced. There is no field anywhere below naming a user, because a field like
that is a field somebody will eventually be able to set.

## Unknown keys are refused rather than dropped

A settings page that silently ignores what it does not recognise is a page that
looks like it saved and did not. The one time that happens to somebody they
stop trusting the screen, so a key nobody knows is a 400 with its name in it.
"""

import json
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from aiohttp import web

from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _load, _store
from bot.handlers.profile.settings_menu.render import _ration, rationed
from bot.handlers.profile.settings_menu.skins import DEFAULT_SKIN
from bot.handlers.profile.settings_menu.typed import FIELDS
from config.settings import TELEGRAM_BOT_TOKEN
from db.database import get_db_session
from services.dossier import preview
from services.dossier import skins as skin_store
from services.miniapp.auth import NotFromTelegram, who
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.render_access import can_use_render
from utils.tenant import get_dm_tenant, user_tenants

logger = get_logger("services.miniapp.api")

# What a page may switch on and off. Named here rather than taken from the
# dataclass, so that a field added to `Choices` for the engine's benefit does
# not become a web-writable setting by accident.
SWITCHES = ("mute", "background", "bare", "leaderboard", "map_hitsounds")

# How the page lays them out: the same three groups the bot's tabs use, under
# the same labels. Stated here rather than in the page, so that a setting moved
# between groups moves for both at once — and so the page holds no name, label
# or bound of its own to drift.
GROUPS = (
    ("sts.rnd.tab", ("leaderboard", "background", "bare", "mute", "map_hitsounds")),
    ("sts.qly.tab", ("size", "fps", "dim", "blur", "meter", "cursor")),
    ("sts.snd.tab", ("music", "hitsounds", "volume")),
)

# What a page may type a number or a size into. The parsers come from the bot's
# own prompts, so a range is stated once.
TYPED = tuple(FIELDS)

# Not settable here yet: `skin` is a name that has to exist in somebody's own
# store, and `effects` is a comma-separated list with its own vocabulary. Both
# want a screen of their own rather than a text box, and pretending otherwise
# would let a page write a skin nobody has.
WRITABLE = set(SWITCHES) | set(TYPED)


def _bearer(request: web.Request) -> str:
    """The `initData` out of `Authorization: tma <blob>`."""
    header = request.headers.get("Authorization", "")
    prefix = "tma "
    return header[len(prefix):] if header.startswith(prefix) else ""


async def _caller(request: web.Request) -> int:
    """Whoever opened the page, or a refusal that says nothing useful.

    The reason a signature failed is logged and never sent: telling somebody
    whether they got the signature right or merely let it go stale is the one
    thing worth learning from the outside.
    """
    try:
        return who(_bearer(request), TELEGRAM_BOT_TOKEN)
    except NotFromTelegram as exc:
        logger.info("refused a mini-app request: %s", exc)
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "not signed in"}),
            content_type="application/json",
        ) from exc


async def _tenant(telegram_id: int):
    """Which chat's settings these are.

    A mini-app opens in a private context, so there is no chat in the request
    the way there is on a message. The same two answers the bot uses: the chat
    somebody picked for DMs, or their only one if they have exactly one.
    """
    async with get_db_session() as session:
        picked = await get_dm_tenant(session, telegram_id)
        if picked is not None:
            return picked
        theirs = await user_tenants(session, telegram_id)
        return theirs[0] if len(theirs) == 1 else None


def _as_json(choices: renders.Choices) -> dict[str, Any]:
    return {name: getattr(choices, name) for name in sorted(WRITABLE)}


def _describe(language: str) -> dict[str, Any]:
    """Every writable setting, in the terms a page draws it in.

    `size` is a text field rather than a slider because it is two numbers and a
    separator; everything else typed is a whole number between two bounds, and
    the bounds come off the field that enforces them.
    """
    described: dict[str, Any] = {}
    for name in SWITCHES:
        described[name] = {"kind": "switch", "label": t(f"sts.rnd.{name}", language)}
    for name in TYPED:
        field = FIELDS[name]
        described[name] = {
            "kind": "number" if field.low is not None else "text",
            "label": t(field.label, language),
            "hint": t(field.hint, language),
            "low": field.low,
            "high": field.high,
        }
    return described


def install(app: web.Application) -> bool:
    """Add the mini-app's endpoints. False when there is no token to check with."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE_DEFAULT":
        logger.warning("no bot token — the mini-app's endpoints are not registered")
        return False

    async def read(request: web.Request) -> web.Response:
        telegram_id = await _caller(request)
        if not can_use_render(telegram_id):
            return web.json_response({"error": "no access to rendering"}, status=403)

        tenant = await _tenant(telegram_id)
        choices = await _load(telegram_id, tenant)
        # The screen's own helper rather than a second session and a second
        # call to `heavy_left`. Same rule, same answer, one place.
        left = await _ration(telegram_id, tenant)
        language = (await get_language(telegram_id)).lower()
        return web.json_response({
            "settings": _as_json(choices),
            # Everything the page needs to draw a control it has never heard
            # of: what kind it is, what to call it, and — for a slider — the
            # bounds its own parser enforces. The page holds none of this, so
            # a setting renamed, relabelled or re-bounded moves by itself.
            "fields": _describe(language),
            "groups": [
                {"label": t(label, language), "keys": [k for k in keys]}
                for label, keys in GROUPS
            ],
            "heavy_left": left,
            "linked": bool(tenant),
            "language": language,
            # The row that opens the grid. Not among the fields above: a skin
            # is a name that has to exist in somebody's own store, so it is
            # chosen from a list rather than typed, and it has its own endpoint
            # that checks the store.
            "skin_heading": t("sts.skn.tab", language),
            "skin_label": t("sts.rnd.skin_default", language)
            if not choices.skin
            else choices.skin,
        })

    async def write(request: web.Request) -> web.Response:
        telegram_id = await _caller(request)
        if not can_use_render(telegram_id):
            return web.json_response({"error": "no access to rendering"}, status=403)
        try:
            asked = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"code": "unreadable"}, status=400)
        if not isinstance(asked, dict) or not asked:
            return web.json_response({"code": "empty"}, status=400)

        language = (await get_language(telegram_id)).lower()
        unknown = sorted(set(asked) - WRITABLE)
        if unknown:
            # Named, and refused. A page that quietly drops what it does not
            # know is a page that looks like it saved.
            return web.json_response(
                {"code": "unknown-key", "error": t("sts.rnd.unknown", language),
                 "keys": unknown},
                status=400,
            )

        tenant = await _tenant(telegram_id)
        choices = await _load(telegram_id, tenant)
        changes: dict[str, Any] = {}
        for key, value in asked.items():
            if key in SWITCHES:
                if not isinstance(value, bool):
                    return web.json_response(
                        {"code": "not-a-switch",
                         "error": t("sts.rnd.unknown", language), "key": key},
                        status=400,
                    )
                changes[key] = value
                continue
            # `None` is "as it comes" — the engine's own default, which is a
            # real choice and the one a fresh account has.
            if value is None:
                changes[key] = None
                continue
            parsed = FIELDS[key].parse(str(value))
            if parsed is None:
                # The sentence the typed prompts use, with the same hint after
                # it. Somebody who has met this in the bot meets the same words
                # here, and neither place invents its own.
                return web.json_response(
                    {
                        "code": "out-of-range",
                        "error": t(
                            "sts.typed.no", language,
                            hint=t(FIELDS[key].hint, language),
                        ),
                        "key": key,
                    },
                    status=400,
                )
            changes[key] = parsed

        wanted = replace(choices, **changes)
        # The same guard the buttons and the typed prompts pass through. A rule
        # with a way round it is not a rule, and a web page is the third way in.
        refusal = await rationed(telegram_id, tenant, choices, wanted, language)
        if refusal:
            # Already a sentence in the reader's language — `rationed` writes it.
            return web.json_response({"code": "rationed", "error": refusal}, status=409)

        for key, value in changes.items():
            setattr(choices, key, value)
        await _store(telegram_id, tenant, choices)
        logger.info("mini-app: %s changed %s", telegram_id, ", ".join(sorted(changes)))
        return web.json_response({"settings": _as_json(choices)})

    async def skin_list(request: web.Request) -> web.Response:
        telegram_id = await _caller(request)
        if not can_use_render(telegram_id):
            return web.json_response({"error": "no access to rendering"}, status=403)

        language = (await get_language(telegram_id)).lower()
        tenant = await _tenant(telegram_id)
        choices = await _load(telegram_id, tenant)
        mine, shared = skin_store.by_owner(telegram_id)
        stale = set(skin_store.stale())

        def described(name: str) -> dict[str, Any]:
            return {
                "name": name,
                "label": t("sts.rnd.skin_default", language)
                if name == DEFAULT_SKIN
                else name,
                # `None` where there is nothing to show: a skin with no hit
                # circle gets its name in the grid rather than a picture of
                # our own fallbacks pretending to be its.
                "preview": f"/app/preview/{quote(name)}.png"
                if preview.path_of(name)
                else None,
                # Unpacked by code older than what is running. The store keeps
                # no `.osk` to redo them from, so the only way back is somebody
                # sending the archive again — which marking is what lets
                # somebody be asked for.
                "stale": name in stale,
            }

        return web.json_response({
            "current": choices.skin or DEFAULT_SKIN,
            # The engine's own look leads the shared list rather than getting a
            # heading to itself: it belongs to nobody, which is what shared
            # means. Same split and same order as the bot's own picker.
            "mine": [described(name) for name in mine],
            "shared": [described(name) for name in [DEFAULT_SKIN, *shared]],
            "headings": {
                "mine": t("sts.skn.mine", language),
                "shared": t("sts.skn.shared", language),
                "none_yours": t("sts.skn.none_yours", language),
            },
        })

    async def skin_choose(request: web.Request) -> web.Response:
        telegram_id = await _caller(request)
        if not can_use_render(telegram_id):
            return web.json_response({"error": "no access to rendering"}, status=403)
        try:
            asked = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"code": "unreadable"}, status=400)

        wanted = (asked or {}).get("name") if isinstance(asked, dict) else None
        language = (await get_language(telegram_id)).lower()
        # The store is the authority, not the page: a grid outlives the skin it
        # was drawn for, exactly as a keyboard does.
        if wanted != DEFAULT_SKIN and not skin_store.folder_of(wanted or ""):
            return web.json_response(
                {"code": "gone", "error": t("sts.rnd.skin_gone", language)}, status=404
            )

        tenant = await _tenant(telegram_id)
        choices = await _load(telegram_id, tenant)
        choices.skin = None if wanted == DEFAULT_SKIN else wanted
        await _store(telegram_id, tenant, choices)
        logger.info("mini-app: %s chose skin %s", telegram_id, wanted)
        return web.json_response({"current": wanted})

    async def skin_preview(request: web.Request) -> web.Response:
        """A skin's thumbnail, to anybody who asks.

        Deliberately not behind the signature the rest of this is: a grid loads
        these with `<img src>`, which cannot carry an Authorization header, and
        the alternatives — a token in a query string, or fetching each as a
        blob — buy nothing here. What a request can learn is that this host has
        a skin by that name, which anybody who can use the bot already knows.

        The name still reaches no filesystem: `path_of` looks it up in the
        store's own listing, so anything that is not a skin is not a file.
        """
        where = preview.path_of(request.match_info.get("name", ""))
        if not where:
            return web.Response(status=404, text="no such preview")
        return web.FileResponse(
            where, headers={"Cache-Control": "public, max-age=86400"}
        )

    app.router.add_get("/app/api/settings", read)
    app.router.add_post("/app/api/settings", write)
    app.router.add_get("/app/api/skins", skin_list)
    app.router.add_post("/app/api/skin", skin_choose)
    app.router.add_get("/app/preview/{name}.png", skin_preview)
    logger.info("mini-app endpoints ready")
    return True


__all__ = ["install", "SWITCHES", "TYPED", "WRITABLE"]
