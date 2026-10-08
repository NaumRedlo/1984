import json

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from services import lava_top as lava


INVOICE = "c5a0cacc-3453-44b0-9532-aa492f1ba191"
RENEWAL = "d41db415-ad71-4f2a-8d8c-27eefee91e66"
OFFER = "836b9fc5-7ae9-4a27-9642-592bc44072b7"
CONFIG = lava.LavaConfig(True, "test-outgoing-key", "test-incoming-key")


@pytest_asyncio.fixture
async def api(monkeypatch):
    calls = []
    reply = {"status": 200, "body": {"id": INVOICE, "paymentUrl": "https://pay.example/checkout"}}

    async def handler(request):
        body = await request.json() if request.can_read_body else None
        calls.append((request.method, request.path, dict(request.query), body, request.headers.get("X-Api-Key")))
        if reply["status"] == 204:
            return web.Response(status=204)
        return web.json_response(reply["body"], status=reply["status"], headers=reply.get("headers"))

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    async with TestServer(app) as server:
        monkeypatch.setattr(lava, "API_URL", str(server.make_url("/")).rstrip("/"))
        async with aiohttp.ClientSession() as session:
            yield lava.LavaClient(session, CONFIG), calls, reply


async def test_checkout_uses_v3_and_explicit_subscription_period(api):
    client, calls, _ = api
    checkout = await client.create_subscription(lava.SubscriptionOffer(OFFER, "RUB", "MONTHLY"), "buyer@example.org", return_url="https://dossier.example/billing")
    assert checkout == lava.Checkout(INVOICE, "https://pay.example/checkout")
    method, path, query, body, key = calls[0]
    assert (method, path, query, key) == ("POST", "/api/v3/invoice", {}, CONFIG.api_key)
    assert body == {
        "offerId": OFFER, "email": "buyer@example.org", "currency": "RUB", "periodicity": "MONTHLY",
        "successful_return_url": "https://dossier.example/billing",
        "failure_return_url": "https://dossier.example/billing",
        "cancel_return_url": "https://dossier.example/billing",
    }


async def test_lookup_and_cancel_use_parent_contract_and_saved_email(api):
    client, calls, reply = api
    await client.invoice(INVOICE)
    await client.subscription(INVOICE)
    reply["status"] = 204
    await client.cancel_subscription(INVOICE, "buyer+tag@example.org")
    assert [(c[0], c[1]) for c in calls] == [
        ("GET", f"/api/v2/invoices/{INVOICE}"),
        ("GET", f"/api/v1/subscriptions/{INVOICE}"),
        ("DELETE", "/api/v1/subscriptions"),
    ]
    assert calls[-1][2] == {"contractId": INVOICE, "email": "buyer+tag@example.org"}


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_errors_do_not_leak_provider_body_or_retry_creation(api, status):
    client, calls, reply = api
    reply.update(status=status, body={"error": "buyer@example.org test-outgoing-key"})
    with pytest.raises(lava.LavaError) as exc:
        await client.create_subscription(lava.SubscriptionOffer(OFFER, "EUR", "PERIOD_YEAR"), "buyer@example.org")
    assert exc.value.status == status
    assert "buyer@" not in str(exc.value)
    assert CONFIG.api_key not in str(exc.value)
    assert len(calls) == 1


async def test_redirect_is_not_followed_with_credentials(api):
    client, calls, reply = api
    reply.update(status=307, headers={"Location": "/unexpected"})
    with pytest.raises(lava.LavaError) as exc:
        await client.invoice(INVOICE)
    assert exc.value.status == 307
    assert len(calls) == 1


@pytest.mark.parametrize("body", [[], {"id": "bad"}, {"id": INVOICE, "paymentUrl": "javascript:alert(1)"}])
async def test_invalid_provider_response(api, body):
    client, _, reply = api
    reply["body"] = body
    with pytest.raises(lava.LavaError):
        await client.create_subscription(lava.SubscriptionOffer(OFFER, "USD", "MONTHLY"), "buyer@example.org")


async def test_disabled_by_default_makes_no_requests(api):
    client, calls, _ = api
    disabled = lava.LavaClient(client.session, lava.LavaConfig.from_env({}))
    with pytest.raises(lava.LavaError, match="disabled"):
        await disabled.invoice(INVOICE)
    assert calls == []


@pytest.mark.parametrize("url", ["http://example.org", "https://user:pass@example.org", "/billing", "javascript:alert(1)"])
async def test_invalid_return_urls_fail_before_request(api, url):
    client, calls, _ = api
    with pytest.raises(ValueError):
        await client.create_subscription(lava.SubscriptionOffer(OFFER, "RUB", "MONTHLY"), "buyer@example.org", return_url=url)
    assert calls == []


def test_configuration_requires_separate_keys_and_hides_them():
    with pytest.raises(ValueError):
        lava.LavaConfig.from_env({"LAVA_TOP_ENABLED": "true"})
    with pytest.raises(ValueError):
        lava.LavaConfig(True, "same", "same")
    assert CONFIG.api_key not in repr(CONFIG)
    assert CONFIG.webhook_key not in repr(CONFIG)


def payment(kind="payment.success", **extra):
    return {"eventType": kind, "contractId": INVOICE, "timestamp": "2024-02-05T08:44:32.42176Z", "status": "subscription-active", **extra}


def event(payload, key=CONFIG.webhook_key):
    return lava.webhook_event(json.dumps(payload).encode(), key, CONFIG)


@pytest.mark.parametrize("key", [None, "", "wrong", CONFIG.api_key])
def test_webhook_authentication_precedes_json_parsing(key):
    with pytest.raises(PermissionError):
        lava.webhook_event(b"not json", key, CONFIG)


def test_repeated_payment_has_stable_key_but_renewal_is_distinct():
    first = event(payment())
    again = event(payment(buyer={"email": "buyer@example.org"}))
    renewal = event(payment("subscription.recurring.payment.success", contractId=RENEWAL, parentContractId=INVOICE))
    assert first.deduplication_key == again.deduplication_key
    assert first.deduplication_key != renewal.deduplication_key
    assert renewal.parent_invoice_id == first.invoice_id
    assert "buyer@" not in repr(again)


def test_cancel_preserves_provider_expiry_instead_of_ending_access_now():
    cancelled = event({"eventType": "subscription.cancelled", "contractId": INVOICE, "cancelledAt": "2024-02-05T08:44:49Z", "willExpireAt": "2024-03-06T08:44:49Z"})
    assert cancelled.expires_at > cancelled.occurred_at


@pytest.mark.parametrize("kind", ["refund.success", "chargeback.initiated"])
def test_adjustments_do_not_guess_invoice_from_buyer_email(kind):
    result = event({"event_type": kind, "event_id": RENEWAL, "created_at": "2024-02-05T08:44:49Z", "data": {"customer_email": "buyer@example.org"}})
    assert result.invoice_id is None
    assert result.parent_invoice_id is None
    assert result.deduplication_key == f"{kind}:{RENEWAL}"


def test_unknown_authenticated_events_can_be_acknowledged():
    assert event({"event_type": "new.event"}) is None


@pytest.mark.parametrize("payload", [
    [], {}, payment(contractId="invalid"), payment(timestamp="2024-02-05T08:44:32"),
    payment("subscription.recurring.payment.success"),
    {"event_type": "refund.success", "event_id": RENEWAL, "created_at": "2024-02-05T08:44:49Z", "data": []},
])
def test_malformed_known_events_are_rejected(payload):
    with pytest.raises(ValueError):
        event(payload)


def test_oversized_webhook_is_rejected():
    with pytest.raises(ValueError, match="too large"):
        lava.webhook_event(b" " * (lava.MAX_BODY + 1), CONFIG.webhook_key, CONFIG)


@pytest.mark.parametrize("period,currency", [("ONE_TIME", "RUB"), ("monthly", "RUB"), ("MONTHLY", "BTC")])
def test_subscription_offer_rejects_one_time_or_unknown_terms(period, currency):
    with pytest.raises(ValueError):
        lava.SubscriptionOffer(OFFER, currency, period)
