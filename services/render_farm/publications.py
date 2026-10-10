import re
import uuid
from datetime import datetime, timezone

from aiohttp import web
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from db.database import AsyncSessionFactory
from db.models.map_publication import MapPublication
from db.models.player import Player
from services.render_farm.videos import player_of


def clean(kind, content):
    if kind not in {"pool", "collection"} or not isinstance(content, dict):
        raise ValueError("invalid publication")
    name = content.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
        raise ValueError("name required (up to 200 characters)")
    if kind == "collection":
        hashes = content.get("hashes")
        if not isinstance(hashes, list) or not 1 <= len(hashes) <= 10000:
            raise ValueError("collection must contain 1–10000 maps")
        if any(not isinstance(h, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", h) for h in hashes):
            raise ValueError("invalid map hash")
        return {"name": name.strip(), "hashes": list(dict.fromkeys(h.lower() for h in hashes))}
    if content.get("format") != "dossier-pool" or content.get("version") != 1:
        raise ValueError("unsupported pool")
    if content.get("collection", False) is not False:
        raise ValueError("collections must be published separately")
    compiler = content.get("compiler", "")
    if not isinstance(compiler, str) or len(compiler) > 50:
        raise ValueError("invalid compiler")
    categories = content.get("categories", {})
    if not isinstance(categories, dict) or len(categories) > 100:
        raise ValueError("invalid categories")
    for category, colour in categories.items():
        if not isinstance(category, str) or not 1 <= len(category) <= 30 or not isinstance(colour, list) or len(colour) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in colour):
            raise ValueError("invalid category")
    order = content.get("category_order", [])
    if not isinstance(order, list) or len(order) > 106 or any(not isinstance(name, str) or len(name) > 30 for name in order):
        raise ValueError("invalid category order")
    slots = content.get("slots")
    if not isinstance(slots, list) or not 1 <= len(slots) <= 500:
        raise ValueError("pool must contain 1–500 slots")
    if not any(isinstance(s, dict) and s.get("hash") for s in slots):
        raise ValueError("pool is empty")
    for slot in slots:
        if not isinstance(slot, dict) or slot.get("mods") not in {"Nm", "Hd", "Hr", "Dt", "Fm", "Tb"}:
            raise ValueError("invalid slot")
        h = slot.get("hash")
        if h is not None and (not isinstance(h, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", h)):
            raise ValueError("invalid map hash")
        for key in ("artist", "title", "version", "note", "category"):
            if not isinstance(slot.get(key, ""), str) or len(slot.get(key, "")) > 1000:
                raise ValueError("invalid map text")
        colour = slot.get("colour")
        if colour is not None and (not isinstance(colour, list) or len(colour) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in colour)):
            raise ValueError("invalid category colour")
    authors = content.get("authors", [])
    if not isinstance(authors, list) or len(authors) > 32 or any(not isinstance(n, str) or not 1 <= len(n) <= 50 for n in authors):
        raise ValueError("invalid authors")
    if content.get("frame") not in {"Free", "Duel", "Stage"}:
        raise ValueError("invalid pool frame")
    backdrop = content.get("backdrop")
    if backdrop is not None:
        if not isinstance(backdrop, dict) or set(backdrop) - {"dim", "blur", "picture"}:
            raise ValueError("invalid backdrop")
        if type(backdrop.get("dim")) is not int or not 30 <= backdrop["dim"] <= 90 or type(backdrop.get("blur")) is not bool:
            raise ValueError("invalid backdrop")
        picture = backdrop.get("picture")
        if not isinstance(picture, str) or not 1 <= len(picture) <= BACKDROP_MOST or not re.fullmatch(r"[A-Za-z0-9_-]+", picture):
            raise ValueError("invalid backdrop picture")
        if not picture.startswith("_9j_"):
            raise ValueError("backdrop picture must be a JPEG")
    return {**content, "name": name.strip()}


async def author_links(session, names):
    result = []
    for name in dict.fromkeys(names):
        player = (await session.execute(select(Player).where(func.lower(Player.osu_username) == name.lower()).order_by(Player.id).limit(1))).scalar_one_or_none()
        result.append({"name": player.osu_username if player else name, "player_id": player.id if player else None, "osu_id": player.osu_user_id if player else None})
    return result


BACKDROP_MOST = 330_000
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def code_of(publication_id: str) -> str:
    number = int(publication_id[:10], 16)
    return "".join(CODE_LETTERS[(number >> shift) & 31] for shift in range(35, -1, -5))


def body(row, owner):
    return {"id": row.id, "code": code_of(row.id), "kind": row.kind, "local_id": row.local_id, "revision": row.revision, "name": row.name, "content": row.content, "authors": row.authors, "owner_id": row.owner_id, "mine": row.owner_id == owner, "updated_at": row.updated_at.isoformat()}


def routes(guard, who):
    async def identity(request):
        denied = await guard(request)
        if denied is not None:
            return denied
        owner = await who(request)
        if owner is None:
            raise web.HTTPUnauthorized()
        async with AsyncSessionFactory() as session:
            player = await player_of(session, owner)
        if player is None:
            raise web.HTTPForbidden(text="linked player profile required")
        return player.id

    async def listed(request):
        owner = await identity(request)
        if isinstance(owner, web.StreamResponse):
            return owner
        kind = request.match_info["kind"]
        if kind not in {"pool", "collection"}:
            raise web.HTTPBadRequest()
        try:
            offset = max(0, int(request.query.get("offset", "0")))
        except ValueError:
            raise web.HTTPBadRequest()
        async with AsyncSessionFactory() as session:
            rows = (await session.execute(select(MapPublication).where(MapPublication.kind == kind).order_by(MapPublication.updated_at.desc(), MapPublication.id).offset(offset).limit(100))).scalars().all()
            return web.json_response([body(row, owner) for row in rows])

    async def saved(request):
        owner = await identity(request)
        if isinstance(owner, web.StreamResponse):
            return owner
        kind, key = request.match_info["kind"], request.match_info["key"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise web.HTTPBadRequest()
        raw = bytearray()
        async for chunk in request.content.iter_chunked(65536):
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise web.HTTPRequestEntityTooLarge(max_size=1024 * 1024, actual_size=len(raw))
        import json
        try:
            value = json.loads(raw)
            content = clean(kind, value["content"])
            revision = value["revision"]
            if type(revision) is not int or revision < 0:
                raise ValueError("invalid revision")
        except (ValueError, TypeError, KeyError) as error:
            return web.json_response({"error": str(error)}, status=400)
        async with AsyncSessionFactory() as session:
            row = (await session.execute(select(MapPublication).where(MapPublication.owner_id == owner, MapPublication.kind == kind, MapPublication.local_id == key))).scalar_one_or_none()
            names = content.get("authors", []) + ([content["compiler"]] if content.get("compiler") else [])
            authors = await author_links(session, names)
            now = datetime.now(timezone.utc)
            if row is None:
                if revision != 0:
                    raise web.HTTPConflict(text="publication was removed; refresh catalogue")
                row = MapPublication(id=uuid.uuid4().hex, owner_id=owner, kind=kind, local_id=key, revision=1, name=content["name"], content=content, authors=authors, updated_at=now)
                session.add(row)
            else:
                if row.content == content:
                    return web.json_response(body(row, owner))
                changed = await session.execute(update(MapPublication).where(MapPublication.id == row.id, MapPublication.revision == revision).values(revision=revision + 1, name=content["name"], content=content, authors=authors, updated_at=now))
                if changed.rowcount != 1:
                    raise web.HTTPConflict(text="publication changed; refresh catalogue")
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise web.HTTPConflict(text="publication changed; refresh catalogue")
            await session.refresh(row)
            return web.json_response(body(row, owner))

    async def removed(request):
        owner = await identity(request)
        if isinstance(owner, web.StreamResponse):
            return owner
        async with AsyncSessionFactory() as session:
            await session.execute(delete(MapPublication).where(MapPublication.owner_id == owner, MapPublication.kind == request.match_info["kind"], MapPublication.local_id == request.match_info["key"]))
            await session.commit()
        return web.Response(status=204)

    return [web.get("/render/catalog/{kind}", listed), web.put("/render/catalog/{kind}/{key}", saved), web.delete("/render/catalog/{kind}/{key}", removed)]
