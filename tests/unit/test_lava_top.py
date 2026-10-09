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
        calls.append((request.method, request.path, request.query.copy(), body, request.headers.get("X-Api-Key")))
        if "pages" in reply:
            return web.json_response(reply["pages"].pop(0))
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


async def test_checkout_carries_our_own_reference_so_a_lost_answer_can_be_matched_later(api):
    client, calls, _ = api
    await client.create_subscription(lava.SubscriptionOffer(OFFER, "RUB", "MONTHLY"), "buyer@example.org", ref="a1b2c3d4e5f60718293a4b5c6d7e8f90")
    assert calls[0][3]["clientUtm"] == {"utm_source": "dossier", "utm_content": "a1b2c3d4e5f60718293a4b5c6d7e8f90"}
    await client.create_subscription(lava.SubscriptionOffer(OFFER, "RUB", "MONTHLY"), "buyer@example.org")
    assert "clientUtm" not in calls[1][3]


@pytest.mark.parametrize("ref", ["", "has space", "ключ", "x" * 65, "a-b", 7, None.__class__])
async def test_an_unsafe_checkout_reference_fails_before_any_request(api, ref):
    client, calls, _ = api
    with pytest.raises(ValueError):
        await client.create_subscription(lava.SubscriptionOffer(OFFER, "RUB", "MONTHLY"), "buyer@example.org", ref=ref)
    assert calls == []


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


def test_configuration_requires_api_key_and_distinct_webhook_key_and_hides_them():
    with pytest.raises(ValueError):
        lava.LavaConfig.from_env({"LAVA_TOP_ENABLED": "true"})
    with pytest.raises(ValueError):
        lava.LavaConfig(True, "same", "same")
    assert CONFIG.api_key not in repr(CONFIG)
    assert CONFIG.webhook_key not in repr(CONFIG)


def test_read_only_configuration_cannot_accept_webhooks():
    config = lava.LavaConfig.from_env({"LAVA_TOP_ENABLED": "true", "LAVA_TOP_API_KEY": "test-key"})
    assert config.enabled
    with pytest.raises(PermissionError):
        lava.webhook_event(b"{}", "test-key", config)


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


def product(product_id=INVOICE, offer_id=OFFER, **extra):
    return {"id": product_id, "type": "SUBSCRIPTION", "title": "Dossier", "offers": [{"id": offer_id, "name": "Plus", "prices": [
        {"amount": 199.99, "currency": "RUB", "periodicity": "MONTHLY"},
        {"amount": 19, "currency": "EUR", "periodicity": "PERIOD_YEAR"},
    ]}], **extra}


def product_page(products, next_page=None):
    return {"items": [{"type": "PRODUCT", "data": p} for p in products], "nextPage": next_page}


def flat_page(products, next_page=None):
    return {"items": list(products), "nextPage": next_page}


@pytest.mark.parametrize("page", [product_page, flat_page])
async def test_a_product_page_may_wrap_each_product_or_list_it_directly(api, page):
    client, calls, reply = api
    reply["body"] = page([product(), product(RENEWAL, RENEWAL)])
    products = await client.products()
    assert [row["id"] for row in products] == [INVOICE, RENEWAL]
    assert [price.key for price in lava.catalogue_prices(products)] == [
        f"{OFFER}:RUB:MONTHLY", f"{OFFER}:EUR:PERIOD_YEAR", f"{RENEWAL}:RUB:MONTHLY", f"{RENEWAL}:EUR:PERIOD_YEAR",
    ]


async def test_wrapped_and_direct_products_may_share_a_page(api):
    client, calls, reply = api
    reply["body"] = {"items": [{"type": "PRODUCT", "data": product()}, product(RENEWAL)], "nextPage": None}
    assert len(await client.products()) == 2


@pytest.mark.parametrize("item", [
    "text", None, [],
    {"type": "POST", "data": {"id": INVOICE}},
    {"type": "PRODUCT", "data": "text"},
    {"type": "PRODUCT", "id": INVOICE},
    {"type": "POST", "id": INVOICE},
    {"id": INVOICE},
    {"type": 7, "id": INVOICE},
    {"type": "SUBSCRIPTION"},
    {"type": "SUBSCRIPTION", "id": "not-a-uuid"},
])
async def test_a_product_page_with_an_unrecognised_item_is_rejected(api, item):
    client, calls, reply = api
    reply["body"] = {"items": [item], "nextPage": None}
    with pytest.raises(lava.LavaError, match="Invalid lava.top product"):
        await client.products()


async def test_all_product_pages_keep_long_periods_and_hidden_products(api):
    client, calls, reply = api
    reply["pages"] = [product_page([product()], lava.API_URL + "/api/v2/products?beforeCreatedAt=2024-01-01T00%3A00%3A00Z"), product_page([product(RENEWAL)])]
    assert len(await client.products()) == 2
    assert len(calls) == 2
    assert all(call[2]["showAllSubscriptionPeriods"] == "true" and call[2]["feedVisibility"] == "ALL" for call in calls)
    assert calls[1][2]["beforeCreatedAt"] == "2024-01-01T00:00:00Z"


@pytest.mark.parametrize("suffix", ["https://other.example/api/v2/products?beforeCreatedAt=2024-01-01T00:00:00Z", "/api/v2/products", "http://gate.lava.top/api/v2/products"])
async def test_untrusted_pagination_link_is_never_requested(api, suffix):
    client, calls, reply = api
    reply["body"] = product_page([], suffix)
    with pytest.raises(lava.LavaError, match="cursor"):
        await client.products()
    assert len(calls) == 1


async def test_repeated_product_cursor_is_rejected(api):
    client, calls, reply = api
    reply["body"] = product_page([], lava.API_URL + "/api/v2/products?beforeCreatedAt=2024-01-01T00:00:00Z")
    with pytest.raises(lava.LavaError, match="cursor"):
        await client.products()
    assert len(calls) == 2


async def test_subscription_snapshot_includes_unsuccessful_and_all_pages(api):
    client, calls, reply = api
    reply["pages"] = [
        {"items": [{"id": INVOICE}], "page": 1, "pages": 2, "total": 2},
        {"items": [{"id": RENEWAL}], "page": 2, "pages": 2, "total": 2},
    ]
    assert [s["id"] for s in await client.subscriptions()] == [INVOICE, RENEWAL]
    assert calls[0][2].getall("invoiceStatuses") == ["NEW", "IN_PROGRESS", "COMPLETED", "FAILED"]
    assert calls[1][2]["page"] == "2"


@pytest.mark.parametrize("page", [
    {"items": [], "page": 1, "pages": 2, "total": 1},
    {"items": [], "page": 1, "pages": 1, "total": 1},
    {"items": [], "page": 2, "pages": 2, "total": 0},
    {"items": [], "page": 1, "pages": 201, "total": 0},
])
async def test_incomplete_subscription_snapshot_is_not_returned(api, page):
    client, _, reply = api
    reply["body"] = page
    with pytest.raises(lava.LavaError):
        await client.subscriptions()


async def test_empty_account(api):
    client, _, reply = api
    reply["pages"] = [product_page([]), {"items": [], "page": 1, "pages": 0, "total": 0}]
    assert await client.products() == []
    assert await client.subscriptions() == []


def test_catalogue_prices_preserve_decimal_and_filter_selected_products():
    rows = lava.catalogue_prices([product(), product(RENEWAL)], frozenset({INVOICE}))
    assert [r.amount for r in rows] == ["199.99", "19"]
    assert rows[1].offer().periodicity == "PERIOD_YEAR"
    assert rows[0].key != rows[1].key
    assert lava.catalogue_prices([product()], frozenset()) == []


def test_catalogue_ignores_non_subscription_and_unpriced_offers():
    assert lava.catalogue_prices([product(type="COURSE")]) == []
    assert lava.catalogue_prices([product(offers=None)]) == []


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf"), True])
def test_invalid_catalogue_prices_fail_closed(amount):
    item = product()
    item["offers"][0]["prices"][0]["amount"] = amount
    with pytest.raises(lava.LavaError):
        lava.catalogue_prices([item])


def test_a_refusal_is_quoted_for_the_log_without_the_buyer_address():
    from services.lava_top import said

    quoted = said(b'{"error":"Validation failed",\n "details":{"email":"buyer@example.org is not allowed"}}' + b"x" * 900)
    assert "buyer@example.org" not in quoted and "<email>" in quoted and "Validation failed" in quoted
    assert len(quoted) <= 300 and "\n" not in quoted
