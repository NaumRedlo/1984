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

from aiohttp import web

from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _load, _store
from bot.handlers.profile.settings_menu.render import _ration, rationed
from bot.handlers.profile.settings_menu.typed import FIELDS
from config.settings import TELEGRAM_BOT_TOKEN
from db.database import get_db_session
from services.miniapp.auth import NotFromTelegram, who
from utils.language import get_language
from utils.logger import get_logger
from utils.render_access import can_use_render
from utils.tenant import get_dm_tenant, user_tenants

logger = get_logger("services.miniapp.api")

# What a page may switch on and off. Named here rather than taken from the
# dataclass, so that a field added to `Choices` for the engine's benefit does
# not become a web-writable setting by accident.
SWITCHES = ("mute", "background", "bare", "leaderboard", "map_hitsounds")

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
        return web.json_response({
            "settings": _as_json(choices),
            # What the page needs to draw the controls without knowing the
            # rules: which keys are switches, which take numbers, and how many
            # big renders are left today.
            "switches": list(SWITCHES),
            "typed": list(TYPED),
            "heavy_left": left,
            "linked": bool(tenant),
            "language": (await get_language(telegram_id)).lower(),
        })

    async def write(request: web.Request) -> web.Response:
        telegram_id = await _caller(request)
        if not can_use_render(telegram_id):
            return web.json_response({"error": "no access to rendering"}, status=403)
        try:
            asked = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "not readable"}, status=400)
        if not isinstance(asked, dict) or not asked:
            return web.json_response({"error": "nothing to change"}, status=400)

        unknown = sorted(set(asked) - WRITABLE)
        if unknown:
            # Named, and refused. A page that quietly drops what it does not
            # know is a page that looks like it saved.
            return web.json_response(
                {"error": "not a setting", "keys": unknown}, status=400
            )

        tenant = await _tenant(telegram_id)
        choices = await _load(telegram_id, tenant)
        changes: dict[str, Any] = {}
        for key, value in asked.items():
            if key in SWITCHES:
                if not isinstance(value, bool):
                    return web.json_response(
                        {"error": "not a yes or no", "key": key}, status=400
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
                return web.json_response(
                    {"error": "out of range", "key": key}, status=400
                )
            changes[key] = parsed

        wanted = replace(choices, **changes)
        # The same guard the buttons and the typed prompts pass through. A rule
        # with a way round it is not a rule, and a web page is the third way in.
        language = (await get_language(telegram_id)).lower()
        refusal = await rationed(telegram_id, tenant, choices, wanted, language)
        if refusal:
            return web.json_response({"error": refusal}, status=409)

        for key, value in changes.items():
            setattr(choices, key, value)
        await _store(telegram_id, tenant, choices)
        logger.info("mini-app: %s changed %s", telegram_id, ", ".join(sorted(changes)))
        return web.json_response({"settings": _as_json(choices)})

    app.router.add_get("/app/api/settings", read)
    app.router.add_post("/app/api/settings", write)
    logger.info("mini-app endpoints ready")
    return True


__all__ = ["install", "SWITCHES", "TYPED", "WRITABLE"]
