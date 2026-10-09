import asyncio
import os
import time
from dataclasses import asdict

import aiohttp
from aiohttp import web

from services.lava_top import LavaClient, LavaConfig, LavaError, catalogue_prices, identifier


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


def routes(guard, who, catalogue=None):
    if catalogue is None:
        selected = frozenset(value.strip() for value in os.getenv("LAVA_TOP_PRODUCT_IDS", "").split(",") if value.strip())
        catalogue = Catalogue(LavaConfig.from_env(), selected)

    async def plans(request):
        denied = await guard(request)
        if denied is not None:
            return denied
        if await who(request) is None:
            return web.json_response({"error": "sign in required"}, status=401)
        try:
            prices = await catalogue.prices()
        except LavaError:
            return web.json_response({"error": "billing catalogue unavailable"}, status=503, headers={"Retry-After": "10", "Cache-Control": "no-store"})
        return web.json_response({"plans": [{"id": price.key, **asdict(price)} for price in prices]}, headers={"Cache-Control": "no-store"})

    return [web.get("/render/billing/plans", plans)]
