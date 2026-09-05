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
from config.settings import MINIAPP_ENABLED, TELEGRAM_BOT_TOKEN
from db.database import get_db_session
from dossier import build as engine_build
from services.dossier import preview
from dossier import skins as skin_store
from services.render_farm.queue import queue as render_queue
from services.render_farm.roster import roster as render_roster
from services.miniapp.auth import NotFromTelegram, who
from utils.formatting.text import plural
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.render_access import can_use_render
from utils.tenant import get_dm_tenant, user_tenants

logger = get_logger("services.miniapp.api")

SWITCHES = ("mute", "background", "bare", "leaderboard", "map_hitsounds")

GROUPS = (
    ("sts.rnd.tab", ("leaderboard", "background", "bare", "mute", "map_hitsounds")),
    ("sts.qly.tab", ("size", "fps", "dim", "blur", "meter", "cursor")),
    ("sts.snd.tab", ("music", "hitsounds", "volume")),
)

TYPED = tuple(FIELDS)

WRITABLE = set(SWITCHES) | set(TYPED)

def _bearer(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    prefix = "tma "
    return header[len(prefix):] if header.startswith(prefix) else ""

async def _caller(request: web.Request) -> int:
    try:
        return who(_bearer(request), TELEGRAM_BOT_TOKEN)
    except NotFromTelegram as exc:
        logger.info("refused a mini-app request: %s", exc)
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "not signed in"}),
            content_type="application/json",
        ) from exc

async def _tenant(telegram_id: int):
    async with get_db_session() as session:
        picked = await get_dm_tenant(session, telegram_id)
        if picked is not None:
            return picked
        theirs = await user_tenants(session, telegram_id)
        return theirs[0] if len(theirs) == 1 else None

def _as_json(choices: renders.Choices) -> dict[str, Any]:
    return {name: getattr(choices, name) for name in sorted(WRITABLE)}

def _threads(threads: int, language: str) -> str:
    forms = t("dsr.app.thread_word", language).split("|")
    if len(forms) == 3:
        word = plural(threads, *forms)
    elif len(forms) == 2:
        word = forms[0] if threads == 1 else forms[1]
    else:
        word = forms[0]
    return t("dsr.app.threads", language, threads=threads, word=word)

def _why(worker, language: str) -> str:
    if not worker.code:
        return worker.reason
    said = t(f"dsr.farm.why.{worker.code}", language, detail=worker.detail)

    return worker.reason if said.startswith("dsr.farm.why.") else said

def _words(language: str) -> dict[str, str]:
    return {
        name: t(f"dsr.app.{name}", language)
        for name in (
            "save", "saved", "loading", "as_it_comes", "no_picture",
            "stale_build", "only_in_telegram",
        )
    } | {
        "failed": t("dsr.app.failed", language, why="{why}"),
        "heavy_left": t("dsr.app.heavy_left", language, left="{left}"),
    }

def _describe(language: str) -> dict[str, Any]:
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
    if not MINIAPP_ENABLED:
        logger.info("the mini-app is switched off — its endpoints are not registered")
        return False
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE_DEFAULT":
        logger.warning("no bot token — the mini-app's endpoints are not registered")
        return False

    async def read(request: web.Request) -> web.Response:
        telegram_id = await _caller(request)
        if not can_use_render(telegram_id):
            return web.json_response({"error": "no access to rendering"}, status=403)

        tenant = await _tenant(telegram_id)
        choices = await _load(telegram_id, tenant)

        left = await _ration(telegram_id, tenant)
        language = (await get_language(telegram_id)).lower()
        return web.json_response({
            "settings": _as_json(choices),

            "fields": _describe(language),
            "groups": [
                {"label": t(label, language), "keys": [k for k in keys]}
                for label, keys in GROUPS
            ],
            "heavy_left": left,
            "linked": bool(tenant),
            "language": language,

            "words": _words(language),
            "tabs": {
                "settings": t("dsr.farm.settings_tab", language),
                "skins": t("dsr.app.skins_tab", language),
                "farm": t("dsr.farm.tab", language),
            },
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

            if value is None:
                changes[key] = None
                continue
            parsed = FIELDS[key].parse(str(value))
            if parsed is None:

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

        refusal = await rationed(telegram_id, tenant, choices, wanted, language)
        if refusal:

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

                "preview": f"/app/preview/{quote(name)}.png"
                if preview.path_of(name)
                else None,

                "stale": name in stale,
            }

        return web.json_response({
            "current": choices.skin or DEFAULT_SKIN,

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
        where = preview.path_of(request.match_info.get("name", ""))
        if not where:
            return web.Response(status=404, text="no such preview")
        return web.FileResponse(
            where, headers={"Cache-Control": "public, max-age=86400"}
        )

    async def farm(request: web.Request) -> web.Response:
        telegram_id = await _caller(request)
        if not can_use_render(telegram_id):
            return web.json_response({"error": "no access to rendering"}, status=403)

        language = (await get_language(telegram_id)).lower()
        busy = render_queue.rendering()
        ours = engine_build.build_of(await engine_build.local())
        here = render_roster.here()

        def described(worker) -> dict[str, Any]:
            state = worker.state(rendering=worker.name in busy)
            theirs = engine_build.build_of(worker.build)
            return {
                "name": worker.name,
                "state": state,
                "label": t(f"dsr.farm.{state}", language),
                "threads": worker.threads,

                "threads_label": _threads(worker.threads, language)
                if worker.threads
                else "",

                "reason": _why(worker, language) if state == "resting" else "",
                "delivered": worker.delivered,
                "handed_back": worker.handed_back,

                "stale_build": bool(
                    worker.build
                    and theirs != ours
                    and engine_build.UNKNOWN not in (theirs, ours)
                ),
            }

        return web.json_response({
            "waiting": len(render_queue.waiting()),
            "workers": [described(worker) for worker in here],
            "empty": t("dsr.farm.empty", language),
            "stale_build": t("dsr.app.stale_build", language),
            "loading": t("dsr.app.loading", language),
            "queued": t("dsr.farm.queued", language, waiting=len(render_queue.waiting())),
            "tally": t("dsr.farm.tally", language, delivered="{delivered}", back="{back}"),
        })

    app.router.add_get("/app/api/farm", farm)
    app.router.add_get("/app/api/settings", read)
    app.router.add_post("/app/api/settings", write)
    app.router.add_get("/app/api/skins", skin_list)
    app.router.add_post("/app/api/skin", skin_choose)
    app.router.add_get("/app/preview/{name}.png", skin_preview)
    logger.info("mini-app endpoints ready")
    return True

__all__ = ["install", "SWITCHES", "TYPED", "WRITABLE"]
