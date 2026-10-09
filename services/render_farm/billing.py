import asyncio
import json
import logging
import os
import time
from dataclasses import asdict

import aiohttp
from aiohttp import web

from db.database import AsyncSessionFactory
from services.lava_top import MAX_BODY, LavaClient, LavaConfig, LavaError, catalogue_prices, email_address, identifier, webhook_event
from services.render_farm import subscriptions
from services.render_farm.videos import player_of

log = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}


class Catalogue:
    def __init__(self, config: LavaConfig, product_ids: frozenset[str]):
        self.config = config
        self.product_ids = frozenset(identifier(value) for value in product_ids)
        self._lock = asyncio.Lock()
        self._prices = ()
        self._expires = 0.0
        self._retry_at = 0.0

    async def _fetch(self):
        async with aiohttp.ClientSession() as session:
            return await LavaClient(session, self.config).products()

    async def prices(self):
        if not self.config.enabled or not self.product_ids:
            raise LavaError("Billing catalogue is not configured")
        async with self._lock:
            now = time.monotonic()
            if now < self._expires:
                return self._prices
            if now < self._retry_at:
                raise LavaError("Billing catalogue is temporarily unavailable")
            try:
                products = await asyncio.wait_for(self._fetch(), timeout=30)
                prices = catalogue_prices(products, self.product_ids)
            except (LavaError, asyncio.TimeoutError):
                self._retry_at = time.monotonic() + 10
                raise LavaError("Billing catalogue is temporarily unavailable") from None
            self._prices = tuple(prices)
            self._expires = time.monotonic() + 60
            return self._prices


def routes(guard, who, catalogue=None, provider=None):
    if catalogue is None:
        selected = frozenset(value.strip() for value in os.getenv("LAVA_TOP_PRODUCT_IDS", "").split(",") if value.strip())
        catalogue = Catalogue(LavaConfig.from_env(), selected)
    provider = provider or subscriptions.Provider(catalogue.config)

    async def account(request):
        denied = await guard(request)
        if denied is not None:
            return None, denied
        owner = await who(request)
        if owner is None:
            return None, web.json_response({"error": "sign in required"}, status=401)
        async with AsyncSessionFactory() as session:
            player = await player_of(session, owner)
        if player is None:
            return None, web.json_response({"error": "link your osu! account first"}, status=403)
        return player.id, None

    async def plans(request):
        denied = await guard(request)
        if denied is not None:
            return denied
        if await who(request) is None:
            return web.json_response({"error": "sign in required"}, status=401)
        try:
            prices = await catalogue.prices()
        except LavaError:
            return web.json_response({"error": "billing catalogue unavailable"}, status=503, headers={"Retry-After": "10", **NO_STORE})
        return web.json_response({"plans": [{"id": price.key, **asdict(price)} for price in prices]}, headers=NO_STORE)

    async def current(request):
        player_id, denied = await account(request)
        if denied is not None:
            return denied
        return web.json_response({"subscription": await subscriptions.status(player_id, provider)}, headers=NO_STORE)

    async def checkout(request):
        player_id, denied = await account(request)
        if denied is not None:
            return denied
        try:
            raw = await request.content.read(4097)
            value = json.loads(raw) if 0 < len(raw) <= 4096 else None
            plan, email, change = value["plan"], email_address(value["email"]), value.get("change") is True
            if not isinstance(plan, str) or not 0 < len(plan) <= 120:
                raise ValueError("invalid plan")
        except (ValueError, TypeError, KeyError):
            return web.json_response({"error": "plan and a valid email are required"}, status=400)
        try:
            prices = await catalogue.prices()
        except LavaError:
            return web.json_response({"error": "billing catalogue unavailable"}, status=503, headers={"Retry-After": "10", **NO_STORE})
        price = next((price for price in prices if price.key == plan), None)
        if price is None:
            return web.json_response({"error": "unknown plan"}, status=404)
        try:
            shown = await subscriptions.start(player_id, price, email, provider, change, prices)
        except subscriptions.Refused as refused:
            return web.json_response({"error": refused.reason, "subscription": refused.shown}, status=409, headers=NO_STORE)
        except LavaError:
            return web.json_response({"error": "payment provider unavailable"}, status=502, headers=NO_STORE)
        return web.json_response({"subscription": shown}, status=201, headers=NO_STORE)

    async def cancelled(request):
        player_id, denied = await account(request)
        if denied is not None:
            return denied
        try:
            shown = await subscriptions.cancel(player_id, provider)
        except subscriptions.Refused as refused:
            return web.json_response({"error": refused.reason}, status=409, headers=NO_STORE)
        except LavaError:
            return web.json_response({"error": "payment provider unavailable"}, status=502, headers=NO_STORE)
        return web.json_response({"subscription": shown}, headers=NO_STORE)

    async def webhook(request):
        raw = bytearray()
        async for chunk in request.content.iter_chunked(8192):
            raw.extend(chunk)
            if len(raw) > MAX_BODY:
                return web.Response(status=413)
        try:
            event = webhook_event(bytes(raw), request.headers.get("X-Api-Key"), catalogue.config)
        except PermissionError:
            return web.Response(status=401)
        except (ValueError, UnicodeError):
            return web.Response(status=400)
        if event is not None:
            await subscriptions.receive(event, provider)
        return web.Response(status=204)

    return [
        web.get("/render/billing/plans", plans),
        web.get("/render/billing/status", current),
        web.post("/render/billing/checkout", checkout),
        web.post("/render/billing/cancel", cancelled),
        web.post("/render/billing/webhook", webhook),
    ]
