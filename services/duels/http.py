import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from services.duels import live, notify, series, service, states
from services.duels.errors import DuelError
from services.duels.rules import SLOTS
from utils.logger import get_logger

logger = get_logger(__name__)

STATUS = {
    "NOT_FOUND": 404,
    "NOT_PARTICIPANT": 403,
    "NOT_LINKED": 403,
    "INVALID_STATE": 409,
    "VERSION_CONFLICT": 409,
    "ALREADY_BUSY": 409,
    "ALREADY_INVITED": 409,
    "NOT_YOUR_TURN": 409,
    "SLOT_UNAVAILABLE": 409,
    "MAP_REVISION_CHANGED": 409,
    "OPERATION_REUSED": 422,
    "SELF_CHALLENGE": 422,
    "ROOM_CONTROL_REQUIRED": 409,
}

def _answer(body: Any, status: int = 200) -> web.Response:
    return web.json_response(body, status=status)

def _refused(error: DuelError) -> web.Response:
    return _answer(error.body(), STATUS.get(error.code, 400))

async def _body(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    try:
        said = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(text="not json")
    if not isinstance(said, dict):
        raise web.HTTPBadRequest(text="not an object")
    return said

def _operation(request: web.Request, said: dict[str, Any]) -> str:
    key = request.headers.get("Idempotency-Key") or said.get("operation_id")
    if not isinstance(key, str) or not 8 <= len(key) <= 64:
        raise web.HTTPBadRequest(text="Idempotency-Key is required")
    return key

def _version(said: dict[str, Any]) -> Optional[int]:
    value = said.get("expected_version")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise web.HTTPBadRequest(text="expected_version must be a number")
    return value

def routes(guard, who, factory_of: Callable[[], Any], osu_of: Callable[[], Any] = lambda: None) -> list[web.RouteDef]:
    discovering: dict[str, asyncio.Task] = {}

    def watched(work, what: str) -> asyncio.Task:
        task = asyncio.create_task(work)

        def done(finished: asyncio.Task) -> None:
            if not finished.cancelled() and finished.exception() is not None:
                logger.error("%s stumbled: %s", what, finished.exception(), exc_info=finished.exception())

        task.add_done_callback(done)
        return task

    def handler(work: Callable[[web.Request, int, dict[str, Any]], Awaitable[Any]], status: int = 200):
        async def handle(request: web.Request) -> web.Response:
            denied = await guard(request)
            if denied is not None:
                return denied
            owner = await who(request)
            if owner is None:
                return _answer({"error": "no one"}, 404)
            if owner.player_id is None:
                return _refused(DuelError("NOT_LINKED"))
            said = await _body(request) if request.method != "GET" else {}
            try:
                found = await work(request, owner.player_id, said)
                if isinstance(found, web.StreamResponse):
                    return found
                return _answer(found, status)
            except DuelError as error:
                return _refused(error)
        return handle

    async def invite(request, player, said):
        opponent = said.get("opponent_id")
        if isinstance(opponent, bool) or not isinstance(opponent, int):
            raise web.HTTPBadRequest(text="opponent_id must be a number")
        return await service.invite(factory_of(), player, opponent, _operation(request, said))

    def on_duel(action):
        async def work(request, player, said):
            return await action(request.match_info["duel_id"], player, said, _operation(request, said), _version(said))
        return work

    async def accept(duel_id, player, said, operation, version):
        return await service.accept(factory_of(), duel_id, player, operation, version)

    async def decline(duel_id, player, said, operation, version):
        return await service.decline(factory_of(), duel_id, player, operation, version)

    async def surrender(duel_id, player, said, operation, version):
        current = await service.read(factory_of(), duel_id, player)
        if current["state"] in ("INVITED",):
            raise DuelError("INVALID_STATE", current)
        if "surrender" in current["actions"] and current["state"] in states.BEFORE_PLAY:
            return await service.surrender(factory_of(), duel_id, player, operation, version)
        return await series.give_up(factory_of(), duel_id, player, operation, version)

    async def reinvite(duel_id, player, said, operation, version):
        return await series.reinvite(factory_of(), duel_id, player, operation, version)

    async def pool_ack(duel_id, player, said, operation, version):
        manifest = said.get("manifest_hash")
        if not isinstance(manifest, str):
            raise web.HTTPBadRequest(text="manifest_hash is required")
        return await series.pool_ack(factory_of(), duel_id, player, manifest, operation, version)

    async def draft(duel_id, player, said, operation, version):
        slot = said.get("slot")
        if not isinstance(slot, str):
            raise web.HTTPBadRequest(text="slot is required")
        return await series.draft(factory_of(), duel_id, player, slot, operation, version)

    async def ready(duel_id, player, said, operation, version):
        mods = said.get("tb_mods")
        if mods is not None and (not isinstance(mods, list) or not all(isinstance(mod, str) for mod in mods)):
            raise web.HTTPBadRequest(text="tb_mods must be a list")
        return await series.ready(factory_of(), duel_id, player, operation, tb_mods=mods, expected_version=version)

    async def bonus(duel_id, player, said, operation, version):
        accept = said.get("accept")
        mods = said.get("tb_mods")
        if not isinstance(accept, bool):
            raise web.HTTPBadRequest(text="accept must be true or false")
        if mods is not None and (not isinstance(mods, list) or not all(isinstance(mod, str) for mod in mods)):
            raise web.HTTPBadRequest(text="tb_mods must be a list")
        return await series.bonus(factory_of(), duel_id, player, accept, operation, tb_mods=mods)

    async def external(duel_id, player, said, operation, version):
        link = said.get("match")
        if not isinstance(link, str):
            raise web.HTTPBadRequest(text="match is required")
        return await series.external_room(factory_of(), duel_id, player, link, operation)

    async def profile_me(request, player, said):
        from services.duels import portfolio, rating
        from utils.timeutils import utcnow

        async with factory_of()() as session:
            seen = await portfolio.observations_of(session, player)
            from sqlalchemy import func, select

            from db.models.duel import Duel, DuelParticipant

            mine = (select(Duel.winner_id).join(DuelParticipant, DuelParticipant.duel_id == Duel.id)
                    .where(DuelParticipant.player_id == player, Duel.state == states.COMPLETED, Duel.winner_id.is_not(None)).subquery())
            won = await session.scalar(select(func.count()).select_from(mine).where(mine.c.winner_id == player)) or 0
            lost = await session.scalar(select(func.count()).select_from(mine).where(mine.c.winner_id != player)) or 0
        skills, marks = portfolio.profile(seen, utcnow())
        return {"rating": await rating.standing(factory_of(), player), "portfolio": portfolio.view(skills, marks),
                "observations": len(seen), "won": int(won), "lost": int(lost)}

    async def history(request, player, said):
        from sqlalchemy import select

        from db.models.duel import Duel, DuelParticipant

        whose = request.query.get("player", str(player))
        before = request.query.get("before")
        if not whose.isdigit() or (before is not None and not before.isdigit()):
            raise web.HTTPBadRequest(text="player and before must be numbers")
        async with factory_of()() as session:
            query = (select(Duel).join(DuelParticipant, DuelParticipant.duel_id == Duel.id)
                     .where(DuelParticipant.player_id == int(whose), Duel.state == states.COMPLETED)
                     .order_by(Duel.finalized_at.desc(), Duel.id.desc()).limit(30))
            if before is not None:
                from datetime import datetime, timezone
                query = query.where(Duel.finalized_at < datetime.fromtimestamp(int(before), tz=timezone.utc).replace(tzinfo=None))
            duels = (await session.scalars(query)).all()
            found = []
            for duel in duels:
                people = await service._participants(session, duel.id)
                found.append({"id": duel.id, "mode": duel.mode, "wins": {"A": duel.wins_a, "B": duel.wins_b}, "winner": duel.winner_id,
                              "outcome": duel.outcome_reason, "finished_at": service._stamp(duel.finalized_at),
                              "players": [{"player": p.player_id, "side": p.side} for p in people]})
        return {"duels": found}

    async def give_feedback(duel_id, player, said, operation, version):
        from services.duels import balance

        slot = said.get("slot")
        if not isinstance(slot, str):
            raise web.HTTPBadRequest(text="slot is required")
        return await balance.feedback(factory_of(), duel_id, player, slot, said.get("fit"), said.get("difficulty"), said.get("fairness"),
                                      bool(said.get("technical")), said.get("comment") if isinstance(said.get("comment"), str) else None)

    def admin(work):
        async def run(request, player, said):
            from config.settings import ADMIN_IDS
            from services.duels import balance

            reason = said.get("reason")
            if request.method == "POST" and (not isinstance(reason, str) or not reason.strip()):
                raise web.HTTPBadRequest(text="reason is required")
            try:
                return await work(request, player, said, tuple(ADMIN_IDS))
            except balance.Forbidden as role:
                return web.json_response({"error": "FORBIDDEN", "role": str(role)}, status=403)
        return run

    async def reviews(request, player, said, owners):
        from services.duels import balance

        return {"reviews": await balance.open_reviews(factory_of(), player, owners)}

    async def map_review(request, player, said, owners):
        from services.duels import balance, catalog

        beatmap = request.match_info["beatmap"]
        if not beatmap.isdigit() or said.get("decision") not in ("APPROVE", "MOVE", "REJECT", "QUARANTINE"):
            raise web.HTTPBadRequest(text="decision is APPROVE, MOVE, REJECT or QUARANTINE")
        try:
            return await balance.review_map(factory_of(), player, int(beatmap), said["decision"], said["reason"], owners, said.get("slot"))
        except catalog.CatalogRefusal as refusal:
            return web.json_response({"error": refusal.code}, status=409)

    async def finalize(request, player, said, owners):
        from services.duels import balance, verifier

        match_id, game_id = said.get("match_id"), said.get("game_id")
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (match_id, game_id)):
            raise web.HTTPBadRequest(text="match_id and game_id are required")
        if osu_of() is None:
            return web.json_response({"error": "SERVICE_RECOVERING"}, status=503)
        return {"duel": await balance.manual_finalize(factory_of(), player, request.match_info["duel_id"], match_id, game_id,
                                                       said["reason"], osu_of(), verifier.Budget(), owners)}

    async def operator_cancel(request, player, said, owners):
        from services.duels import balance

        return {"duel": await balance.operator_cancel(factory_of(), player, request.match_info["duel_id"], said["reason"], owners)}

    async def grant_role(request, player, said, owners):
        from services.duels import balance

        if not isinstance(said.get("player_id"), int) or not isinstance(said.get("role"), str):
            raise web.HTTPBadRequest(text="player_id and role are required")
        await balance.grant(factory_of(), player, said["player_id"], said["role"], said["reason"], owners)
        return {"granted": said["role"]}

    def reassess_published():
        from services.duels import balance, importer

        async def run():
            model = await balance.working_model(factory_of())
            if model is not None:
                await importer.reassess(factory_of(), model)

        running = discovering.get("reassess")
        if running is None or running.done():
            discovering["reassess"] = watched(run(), "reassessing catalogue slots")

    async def publish_model(request, player, said, owners):
        from services.duels import balance

        await balance.publish(factory_of(), player, request.match_info["version"], said["reason"], owners)
        reassess_published()
        return {"published": request.match_info["version"]}

    async def delete_model(request, player, said, owners):
        from services.duels import balance

        await balance.delete_model(factory_of(), player, request.match_info["version"], said["reason"], owners)
        return {"deleted": request.match_info["version"]}

    async def train_model(request, player, said, owners):
        from services.duels import balance, training

        await balance.note_training(factory_of(), player, said["reason"], owners)
        running = discovering.get("training")
        if running is not None and not running.done():
            return {"started": False}
        discovering["training"] = watched(training.train(factory_of()), "training Kamerton")
        return {"started": True}

    async def desk_queue(request, player, said, owners):
        from services.duels import balance, desk

        return {"tasks": await desk.queue(factory_of(), player, owners, await balance.working_model(factory_of()))}

    async def desk_catalog(request, player, said, owners):
        from services.duels import desk

        return await desk.coverage(factory_of(), player, owners)

    async def desk_cell(request, player, said, owners):
        from services.duels import desk

        slot, band = request.match_info["slot"], request.match_info["band"]
        if slot not in SLOTS or not band.isdigit() or int(band) >= len(desk.BANDS):
            raise web.HTTPBadRequest(text="slot and band are required")
        return {"maps": await desk.coverage_cell(factory_of(), player, slot, int(band), owners)}

    async def desk_stuck(request, player, said, owners):
        from services.duels import desk

        return {"duels": await desk.stuck(factory_of(), player, owners)}

    async def desk_journal(request, player, said, owners):
        from services.duels import desk

        before = request.query.get("before", "")
        return {"entries": await desk.journal(factory_of(), player, owners, int(before) if before.isdigit() else None)}

    async def desk_people(request, player, said, owners):
        from services.duels import desk

        return {"people": await desk.people(factory_of(), player, owners)}

    async def desk_models(request, player, said, owners):
        from services.duels import desk

        return {"models": await desk.models(factory_of(), player, owners)}

    async def corpus_table(request, player, said, owners):
        from services.duels import balance

        return {"cells": await balance.corpus_table(factory_of(), player, owners)}

    async def corpus_label(request, player, said, owners):
        from services.duels import balance

        beatmap = said.get("beatmap_id")
        if not isinstance(said.get("corpus"), str) or not isinstance(said.get("year"), int) or not isinstance(said.get("stage"), str) \
                or not isinstance(said.get("slot"), str) or not isinstance(said.get("label"), str) \
                or (beatmap is not None and (not isinstance(beatmap, int) or isinstance(beatmap, bool))):
            raise web.HTTPBadRequest(text="corpus, year, stage, slot and label are required")
        return await balance.label_corpus(factory_of(), player, said["corpus"], said["year"], said["stage"], said["slot"], beatmap,
                                          said["label"], said["reason"], owners)

    async def corpus_tournament(request, player, said, owners):
        from services.duels import balance

        if not isinstance(said.get("name"), str) or not isinstance(said.get("year"), int) or not isinstance(said.get("pool"), str):
            raise web.HTTPBadRequest(text="name, year and pool are required")
        return await balance.add_tournament(factory_of(), player, said["name"], said["year"], said["pool"][:20000], said["reason"], owners)

    async def catalog_discover(request, player, said, owners):
        import aiohttp
        from config.settings import DUEL_OSU_FOLDER
        from services.duels import balance, desk, importer, mirrors

        band = said.get("band")
        if not isinstance(band, int) or isinstance(band, bool) or not 0 <= band < len(desk.BANDS):
            raise web.HTTPBadRequest(text="band is required")
        least = desk.BANDS[band]
        most = desk.BANDS[band + 1] if band + 1 < len(desk.BANDS) else 15.0
        await balance.note_discovery(factory_of(), player, least, most, said["reason"], owners)
        running = discovering.get("task")
        if running is not None and not running.done():
            return {"started": False}
        if osu_of() is None:
            return web.json_response({"error": "SERVICE_RECOVERING"}, status=503)

        async def run():
            model = await balance.working_model(factory_of())
            timeout = aiohttp.ClientTimeout(total=120, sock_read=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await importer.discover(factory_of(), mirrors.aiohttp_fetch(session), osu_of(), DUEL_OSU_FOLDER, least, most, 2,
                                        model=model, report=lambda text: None)

        discovering["task"] = watched(run(), "discovering catalogue maps")
        return {"started": True}

    async def rollback_model(request, player, said, owners):
        from services.duels import balance

        version = await balance.rollback(factory_of(), player, said["reason"], owners)
        reassess_published()
        return {"published": version}

    async def running(request, player, said):
        from services.duels import desk

        return await desk.live(factory_of(), watchers=lambda duel_id: len(live.hub.watching.get(duel_id, ())))

    async def roles_me(request, player, said):
        from config.settings import ADMIN_IDS
        from services.duels import desk

        return {"roles": await desk.my_roles(factory_of(), player, tuple(ADMIN_IDS))}

    async def unread(request, player, said):
        return {"notifications": await notify.unread(factory_of(), player)}

    async def seen(request, player, said):
        ids = said.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids) or len(ids) > 200:
            raise web.HTTPBadRequest(text="ids must be a list of numbers")
        return {"seen": await notify.seen(factory_of(), player, ids)}

    async def enter(request, player, said):
        return await service.enter_queue(factory_of(), player, _operation(request, said))

    async def leave(request, player, said):
        return await service.leave_queue(factory_of(), player, _operation(request, said))

    async def one(request, player, said):
        from db.models.duel import Duel

        async with factory_of()() as session:
            duel = await session.get(Duel, request.match_info["duel_id"])
            if duel is None:
                raise DuelError("NOT_FOUND")
            people = await service._participants(session, duel.id)
            if all(person.player_id != player for person in people):
                raise DuelError("NOT_PARTICIPANT")
            return await series.view(session, duel, player)

    async def mine(request, player, said):
        return {"duels": await service.mine(factory_of(), player)}

    async def events(request, player, said):
        after = request.query.get("after", "0")
        if not after.isdigit():
            raise web.HTTPBadRequest(text="after must be a number")
        return {"events": await service.events_after(factory_of(), request.match_info["duel_id"], player, int(after))}

    return [
        live.socket_route(guard, who, factory_of),
        web.post("/render/duels/invitations", handler(invite, 201)),
        web.get("/render/duels", handler(mine)),
        web.get("/render/duels/notifications", handler(unread)),
        web.get("/render/duels/profile/me", handler(profile_me)),
        web.get("/render/duels/history", handler(history)),
        web.get("/render/duels/running", handler(running)),
        web.get("/render/duels/roles/me", handler(roles_me)),
        web.get("/render/admin/duels/queue", handler(admin(desk_queue))),
        web.get("/render/admin/duels/catalog", handler(admin(desk_catalog))),
        web.get("/render/admin/duels/catalog/{slot}/{band}", handler(admin(desk_cell))),
        web.get("/render/admin/duels/stuck", handler(admin(desk_stuck))),
        web.get("/render/admin/duels/journal", handler(admin(desk_journal))),
        web.get("/render/admin/duels/people", handler(admin(desk_people))),
        web.get("/render/admin/duels/models", handler(admin(desk_models))),
        web.get("/render/admin/duels/reviews", handler(admin(reviews))),
        web.post("/render/admin/duels/maps/{beatmap}/review", handler(admin(map_review))),
        web.post("/render/admin/duels/roles", handler(admin(grant_role))),
        web.get("/render/admin/duels/corpus", handler(admin(corpus_table))),
        web.post("/render/admin/duels/corpus/label", handler(admin(corpus_label))),
        web.post("/render/admin/duels/corpus/tournaments", handler(admin(corpus_tournament))),
        web.post("/render/admin/duels/models/{version}/publish", handler(admin(publish_model))),
        web.post("/render/admin/duels/models/rollback", handler(admin(rollback_model))),
        web.post("/render/admin/duels/models/train", handler(admin(train_model))),
        web.post("/render/admin/duels/models/{version}/delete", handler(admin(delete_model))),
        web.post("/render/admin/duels/catalog/discover", handler(admin(catalog_discover))),
        web.post("/render/admin/duels/{duel_id}/cancel", handler(admin(operator_cancel))),
        web.post("/render/admin/duels/{duel_id}/finalize", handler(admin(finalize))),
        web.post("/render/duels/notifications/seen", handler(seen)),
        web.post("/render/duels/queue", handler(enter)),
        web.delete("/render/duels/queue", handler(leave)),
        web.get("/render/duels/{duel_id}", handler(one)),
        web.get("/render/duels/{duel_id}/events", handler(events)),
        web.post("/render/duels/{duel_id}/accept", handler(on_duel(accept))),
        web.post("/render/duels/{duel_id}/decline", handler(on_duel(decline))),
        web.post("/render/duels/{duel_id}/surrender", handler(on_duel(surrender))),
        web.post("/render/duels/{duel_id}/pool-ack", handler(on_duel(pool_ack))),
        web.post("/render/duels/{duel_id}/reinvite", handler(on_duel(reinvite))),
        web.post("/render/duels/{duel_id}/draft-actions", handler(on_duel(draft))),
        web.post("/render/duels/{duel_id}/ready", handler(on_duel(ready))),
        web.post("/render/duels/{duel_id}/bonus-tb", handler(on_duel(bonus))),
        web.post("/render/duels/{duel_id}/external-room", handler(on_duel(external))),
        web.post("/render/duels/{duel_id}/feedback", handler(on_duel(give_feedback))),
    ]
