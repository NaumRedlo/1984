import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.lava_top import LavaConfig, LavaError
from services.render_farm import billing


PRODUCT = "c5a0cacc-3453-44b0-9532-aa492f1ba191"
OFFER = "836b9fc5-7ae9-4a27-9642-592bc44072b7"
CONFIG = LavaConfig(True, "outgoing-test", "incoming-test")


def products(amount=100):
    return [{"id": PRODUCT, "type": "SUBSCRIPTION", "title": "Dossier", "offers": [{"id": OFFER, "name": "Plus", "prices": [{"amount": amount, "currency": "RUB", "periodicity": "MONTHLY"}]}]}]


async def test_concurrent_reads_share_one_fetch_and_refresh_price(monkeypatch):
    catalogue = billing.Catalogue(CONFIG, frozenset({PRODUCT}))
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return products(calls * 100)

    monkeypatch.setattr(catalogue, "_fetch", fetch)
    results = await asyncio.gather(*(catalogue.prices() for _ in range(10)))
    assert calls == 1
    assert all(r[0].amount == "100" for r in results)
    catalogue._expires = 0
    assert (await catalogue.prices())[0].amount == "200"
    assert calls == 2


async def test_failed_refresh_does_not_return_stale_price_and_backs_off(monkeypatch):
    catalogue = billing.Catalogue(CONFIG, frozenset({PRODUCT}))
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise LavaError("private provider details")
        return products()

    monkeypatch.setattr(catalogue, "_fetch", fetch)
    await catalogue.prices()
    catalogue._expires = 0
    for _ in range(2):
        with pytest.raises(LavaError, match="temporarily unavailable"):
            await catalogue.prices()
    assert calls == 2


@pytest.mark.parametrize("config,ids", [(LavaConfig(), frozenset({PRODUCT})), (CONFIG, frozenset())])
async def test_disabled_or_unselected_catalogue_makes_no_request(monkeypatch, config, ids):
    catalogue = billing.Catalogue(config, ids)

    async def fetch():
        pytest.fail("provider must not be contacted")

    monkeypatch.setattr(catalogue, "_fetch", fetch)
    with pytest.raises(LavaError, match="not configured"):
        await catalogue.prices()


async def test_endpoint_requires_account_and_returns_only_tariffs(monkeypatch):
    catalogue = billing.Catalogue(CONFIG, frozenset({PRODUCT}))
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return products()

    monkeypatch.setattr(catalogue, "_fetch", fetch)

    async def guard(request):
        return web.Response(status=403) if request.headers.get("Blocked") else None

    async def who(request):
        return object() if request.headers.get("Account") else None

    app = web.Application()
    app.add_routes(billing.routes(guard, who, catalogue))
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/render/billing/plans")).status == 401
        assert (await client.get("/render/billing/plans", headers={"Blocked": "yes"})).status == 403
        assert calls == 0
        response = await client.get("/render/billing/plans", headers={"Account": "yes"})
        assert response.status == 200
        result = await response.json()
        assert result["plans"][0]["amount"] == "100"
        assert result["plans"][0]["id"] == f"{OFFER}:RUB:MONTHLY"
        assert response.headers["Cache-Control"] == "no-store"
        catalogue._expires = 0
        catalogue._retry_at = float("inf")
        response = await client.get("/render/billing/plans", headers={"Account": "yes"})
        assert response.status == 503
        assert await response.json() == {"error": "billing catalogue unavailable"}


async def test_account_check_prints_no_buyer_data(monkeypatch):
    from scripts import lava_check

    async def listed(self):
        return products()

    async def subscriptions(self):
        return [{"id": PRODUCT, "buyer": {"email": "private@example.org"}, "subscriptionStatus": "ACTIVE"}]

    monkeypatch.setattr(lava_check.LavaClient, "products", listed)
    monkeypatch.setattr(lava_check.LavaClient, "subscriptions", subscriptions)
    result = await lava_check.inspect_account("secret-test")
    assert result["subscriptions_visible_to_api_key"] == 1
    assert result["subscription_statuses"] == {"ACTIVE": 1}
    assert "private@example.org" not in str(result)
    assert "secret-test" not in str(result)
