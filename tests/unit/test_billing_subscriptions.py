import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.billing import BillingEvent, BillingSubscription
from db.models.player import Player
from services.lava_top import Checkout, LavaConfig, LavaError
from services.render_farm import billing, subscriptions
from services.render_farm.invites import Owner


PRODUCT = "c5a0cacc-3453-44b0-9532-aa492f1ba191"
OFFER = "836b9fc5-7ae9-4a27-9642-592bc44072b7"
FIRST = "7ea82675-4ded-4133-95a7-a6efbaf165cc"
RENEWAL = "d41db415-ad71-4f2a-8d8c-27eefee91e66"
SECOND = "04f152b7-63ec-46ff-958e-8a6f5869acd6"
HIGH = "9d0b1c8e-5f0a-4c50-8a57-0d5f3b0c7a11"
PLAN = f"{OFFER}:RUB:MONTHLY"
PRO = f"{HIGH}:RUB:MONTHLY"
CONFIG = LavaConfig(True, "outgoing-test", "incoming-test")
HOOK = {"X-Api-Key": "incoming-test"}


def later(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def catalogue_rows():
    plus = [{"amount": 199, "currency": "RUB", "periodicity": "MONTHLY"}, {"amount": 2, "currency": "USD", "periodicity": "MONTHLY"}, {"amount": 540, "currency": "RUB", "periodicity": "PERIOD_90_DAYS"}]
    pro = [{"amount": 398, "currency": "RUB", "periodicity": "MONTHLY"}, {"amount": 4, "currency": "USD", "periodicity": "MONTHLY"}]
    return [{"id": PRODUCT, "type": "SUBSCRIPTION", "title": "Dossier", "offers": [{"id": OFFER, "name": "Plus", "prices": plus}, {"id": HIGH, "name": "Pro", "prices": pro}]}]


def invoice(status="COMPLETED", sub="ACTIVE", expires=None, **extra):
    return {"id": FIRST, "status": status, "subscriptionStatus": sub, "subscriptionDetails": {"expiredAt": expires or later(days=30)}, **extra}


class Fake:
    def __init__(self):
        self.created = []
        self.cancelled = []
        self.checked = []
        self.invoices = {FIRST: invoice("NEW", None, None)}
        self.failure = None
        self.broken = False

    async def create(self, offer, email, ref):
        self.created.append((offer, email, ref))
        if self.failure is not None:
            raise self.failure
        number = len(self.created)
        invoice_id = FIRST if number == 1 else str(uuid.uuid4())
        return Checkout(invoice_id, f"https://pay.example/checkout/{number}")

    async def invoice(self, invoice_id):
        self.checked.append(invoice_id)
        if self.broken:
            raise LavaError("lava.top connection failed")
        if invoice_id not in self.invoices:
            raise LavaError("lava.top request failed", 404)
        return self.invoices[invoice_id]

    async def cancel(self, invoice_id, email):
        self.cancelled.append((invoice_id, email))


@pytest_asyncio.fixture
async def setup(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(subscriptions, "AsyncSessionFactory", factory)
    monkeypatch.setattr(billing, "AsyncSessionFactory", factory)
    async with factory() as session:
        session.add_all([Player(id=1, telegram_id=1234, osu_user_id=100, osu_username="One"), Player(id=2, osu_user_id=200, osu_username="Two")])
        await session.commit()
    catalogue = billing.Catalogue(CONFIG, frozenset({PRODUCT}))

    async def fetch():
        return catalogue_rows()

    monkeypatch.setattr(catalogue, "_fetch", fetch)
    provider = Fake()

    async def guard(request):
        return None

    async def who(request):
        if request.headers.get("X-Test-Telegram"):
            return Owner(int(request.headers["X-Test-Telegram"]), "Player", None)
        said = request.headers.get("X-Test-Owner")
        return Owner(0, "Player", int(said)) if said else None

    app = web.Application()
    app.add_routes(billing.routes(guard, who, catalogue, provider))
    async with TestClient(TestServer(app)) as client:
        yield client, provider, factory
    await engine.dispose()


ONE = {"X-Test-Owner": "1"}


async def pay(client, headers=ONE, **body):
    return await client.post("/render/billing/checkout", json={"plan": PLAN, "email": "buyer@example.org", **body}, headers=headers)


def event(kind, contract=FIRST, **extra):
    return {"eventType": kind, "contractId": contract, "timestamp": later(minutes=-1), "product": {"id": PRODUCT, "title": "Dossier"}, "buyer": {"email": "buyer@example.org"}, "status": "subscription-active", **extra}


async def hook(client, body, headers=HOOK):
    return await client.post("/render/billing/webhook", data=json.dumps(body), headers=headers)


async def rows(factory, table=BillingSubscription):
    async with factory() as session:
        return list((await session.execute(select(table))).scalars().all())


async def test_checkout_resolves_the_plan_on_the_server_and_keeps_ownership(setup):
    client, provider, factory = setup
    response = await pay(client)
    assert response.status == 201
    shown = (await response.json())["subscription"]
    assert shown["state"] == "pending" and shown["access"] is False
    assert shown["payment_url"] == "https://pay.example/checkout/1"
    assert shown["plan"] == {"id": PLAN, "title": "Dossier", "name": "Plus", "amount": "199", "currency": "RUB", "periodicity": "MONTHLY"}
    assert "email" not in json.dumps(shown)
    offer, email, ref = provider.created[0]
    assert (offer.offer_id, offer.currency, offer.periodicity, email) == (OFFER, "RUB", "MONTHLY", "buyer@example.org")
    [row] = await rows(factory)
    assert (row.player_id, row.invoice_id, row.id, row.state) == (1, FIRST, ref, "pending")


@pytest.mark.parametrize("headers,body,status", [
    ({}, {"plan": PLAN, "email": "a@b.c"}, 401),
    ({"X-Test-Owner": "99"}, {"plan": PLAN, "email": "a@b.c"}, 403),
    (ONE, {"plan": PLAN}, 400),
    (ONE, {"plan": PLAN, "email": "not an email"}, 400),
    (ONE, {"plan": 7, "email": "a@b.c"}, 400),
    (ONE, {"plan": "x" * 200, "email": "a@b.c"}, 400),
    (ONE, {"plan": f"{OFFER}:RUB:PERIOD_YEAR", "email": "a@b.c"}, 404),
    (ONE, {"plan": "free", "email": "a@b.c", "amount": 0}, 404),
])
async def test_checkout_refuses_unknown_people_plans_and_emails_before_calling_the_provider(setup, headers, body, status):
    client, provider, factory = setup
    response = await client.post("/render/billing/checkout", json=body, headers=headers)
    assert response.status == status
    assert provider.created == [] and await rows(factory) == []


async def test_a_telegram_sign_in_without_a_player_id_is_still_a_buyer(setup):
    client, provider, factory = setup
    assert (await pay(client, {"X-Test-Telegram": "1234"})).status == 201
    assert (await rows(factory))[0].player_id == 1


async def test_a_second_checkout_while_one_is_open_returns_the_open_one_and_calls_nothing(setup):
    client, provider, factory = setup
    await pay(client)
    response = await pay(client)
    assert response.status == 409
    body = await response.json()
    assert body["error"] == "busy" and body["subscription"]["payment_url"] == "https://pay.example/checkout/1"
    assert len(provider.created) == 1 and len(await rows(factory)) == 1
    assert (await pay(client, {"X-Test-Owner": "2"})).status == 201


async def test_a_lost_answer_blocks_a_blind_retry_and_a_late_webhook_still_finds_its_owner(setup):
    client, provider, factory = setup
    provider.failure = LavaError("lava.top connection failed; request outcome may be unknown")
    assert (await pay(client)).status == 502
    [row] = await rows(factory)
    assert (row.state, row.invoice_id) == ("unknown", None)
    provider.failure = None
    again = await pay(client)
    assert again.status == 409 and (await again.json())["error"] == "busy"
    assert len(provider.created) == 1
    provider.invoices[FIRST] = invoice()
    paid = event("payment.success", clientUtm={"utm_source": "dossier", "utm_content": row.id})
    assert (await hook(client, paid)).status == 204
    [row] = await rows(factory)
    assert (row.invoice_id, row.state) == (FIRST, "active")
    shown = (await (await client.get("/render/billing/status", headers=ONE)).json())["subscription"]
    assert shown["state"] == "active" and shown["access"] is True


async def test_a_rejected_checkout_is_a_known_failure_and_may_be_tried_again(setup):
    client, provider, factory = setup
    provider.failure = LavaError("lava.top request failed", 422)
    assert (await pay(client)).status == 502
    assert (await rows(factory))[0].state == "failed"
    provider.failure = None
    assert (await pay(client)).status == 201


async def test_status_follows_the_provider_and_asks_it_no_more_than_every_few_seconds(setup):
    client, provider, factory = setup
    assert (await (await client.get("/render/billing/status", headers=ONE)).json()) == {"subscription": None}
    await pay(client)
    provider.checked.clear()
    first = (await (await client.get("/render/billing/status", headers=ONE)).json())["subscription"]
    assert first["state"] == "pending" and provider.checked == [FIRST]
    await client.get("/render/billing/status", headers=ONE)
    assert provider.checked == [FIRST]
    provider.invoices[FIRST] = invoice(expires=later(days=30))
    async with factory() as session:
        row = (await session.execute(select(BillingSubscription))).scalars().one()
        row.checked_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
        await session.commit()
    shown = (await (await client.get("/render/billing/status", headers=ONE)).json())["subscription"]
    assert shown["state"] == "active" and shown["access"] is True and shown["paid_until"].endswith("Z")
    assert shown["payment_url"] is None


async def test_status_belongs_to_the_account_that_asks(setup):
    client, provider, factory = setup
    await pay(client)
    other = await client.get("/render/billing/status", headers={"X-Test-Owner": "2"})
    assert await other.json() == {"subscription": None}
    assert (await client.get("/render/billing/status")).status == 401


@pytest.mark.parametrize("headers,status", [({}, 401), ({"X-Api-Key": "wrong"}, 401), ({"X-Api-Key": "outgoing-test"}, 401)])
async def test_the_webhook_needs_its_own_key_and_reads_nothing_before_it(setup, headers, status):
    client, provider, factory = setup
    response = await client.post("/render/billing/webhook", data="not json at all", headers=headers)
    assert response.status == status
    assert await rows(factory, BillingEvent) == []


async def test_the_webhook_rejects_damaged_known_events_and_oversize_bodies(setup):
    client, provider, factory = setup
    assert (await client.post("/render/billing/webhook", data="{", headers=HOOK)).status == 400
    assert (await hook(client, {"eventType": "payment.success", "contractId": "nope"})).status == 400
    assert (await client.post("/render/billing/webhook", data=b" " * (300 * 1024), headers=HOOK)).status == 413
    assert await rows(factory, BillingEvent) == []


async def test_an_unknown_but_authenticated_event_is_acknowledged_and_not_kept(setup):
    client, provider, factory = setup
    assert (await hook(client, {"eventType": "something.new"})).status == 204
    assert await rows(factory, BillingEvent) == []


async def test_a_payment_event_is_stored_once_and_reconciled_with_the_provider(setup):
    client, provider, factory = setup
    await pay(client)
    provider.invoices[FIRST] = invoice(expires=later(days=30))
    body = event("payment.success")
    assert (await hook(client, body)).status == 204
    assert (await hook(client, body)).status == 204
    events = await rows(factory, BillingEvent)
    assert [item.deduplication_key for item in events] == [f"payment.success:{FIRST}"]
    assert "buyer@example.org" not in json.dumps(events[0].payload) and "buyer" not in events[0].payload
    [row] = await rows(factory)
    assert row.state == "active" and row.paid_until is not None


async def test_a_renewal_reaches_the_first_invoice_through_its_parent_even_out_of_order(setup):
    client, provider, factory = setup
    await pay(client)
    provider.invoices[FIRST] = invoice(expires=later(days=60))
    renewal = event("subscription.recurring.payment.success", RENEWAL, parentContractId=FIRST)
    assert (await hook(client, renewal)).status == 204
    [row] = await rows(factory)
    assert row.state == "active"
    assert abs((row.paid_until - (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=60))).total_seconds()) < 5


async def test_a_cancellation_keeps_access_until_the_paid_through_date(setup):
    client, provider, factory = setup
    await pay(client)
    end = later(days=12)
    provider.invoices[FIRST] = invoice(sub="CANCELLED", expires=end, subscriptionDetails={"expiredAt": end, "cancelledAt": later(minutes=-1)})
    cancelled = {"eventType": "subscription.cancelled", "contractId": FIRST, "cancelledAt": later(minutes=-1), "willExpireAt": end}
    assert (await hook(client, cancelled)).status == 204
    shown = (await (await client.get("/render/billing/status", headers=ONE)).json())["subscription"]
    assert shown["state"] == "cancelled" and shown["access"] is True and shown["cancelled_at"]


async def test_a_cancellation_found_through_a_renewal_id_reaches_the_first_invoice(setup):
    client, provider, factory = setup
    await pay(client)
    provider.invoices[SECOND] = {"id": SECOND, "status": "COMPLETED", "type": "SUBSCRIPTION_RENEWAL", "parentInvoice": {"id": FIRST}}
    provider.invoices[FIRST] = invoice(sub="CANCELLED", expires=later(days=5))
    cancelled = {"eventType": "subscription.cancelled", "contractId": SECOND, "cancelledAt": later(minutes=-1), "willExpireAt": later(days=5)}
    assert (await hook(client, cancelled)).status == 204
    assert (await rows(factory))[0].state == "cancelled"


async def test_a_failed_recurring_payment_stops_a_subscription_that_has_run_out(setup):
    client, provider, factory = setup
    await pay(client)
    provider.invoices[FIRST] = invoice(sub="FAILED", expires=later(days=-1))
    assert (await hook(client, event("subscription.recurring.payment.failed", RENEWAL, parentContractId=FIRST, status="subscription-failed"))).status == 204
    shown = (await (await client.get("/render/billing/status", headers=ONE)).json())["subscription"]
    assert shown["state"] == "failed" and shown["access"] is False


async def test_an_event_for_an_unknown_checkout_is_kept_and_changes_nothing(setup):
    client, provider, factory = setup
    assert (await hook(client, event("payment.success", "11111111-2222-3333-4444-555555555555"))).status == 204
    assert len(await rows(factory, BillingEvent)) == 1 and await rows(factory) == []


async def test_a_provider_outage_still_stores_the_event_and_a_later_status_check_catches_up(setup):
    client, provider, factory = setup
    await pay(client)
    provider.broken = True
    assert (await hook(client, event("payment.success"))).status == 204
    assert len(await rows(factory, BillingEvent)) == 1
    assert (await rows(factory))[0].state == "pending"
    provider.broken = False
    provider.invoices[FIRST] = invoice()
    async with factory() as session:
        row = (await session.execute(select(BillingSubscription))).scalars().one()
        row.checked_at = None
        await session.commit()
    shown = (await (await client.get("/render/billing/status", headers=ONE)).json())["subscription"]
    assert shown["state"] == "active"


@pytest.mark.parametrize("kind", ["refund.success", "chargeback.initiated"])
async def test_refunds_and_chargebacks_are_stored_but_never_revoke_access_by_guess(setup, kind):
    client, provider, factory = setup
    await pay(client)
    provider.invoices[FIRST] = invoice()
    await hook(client, event("payment.success"))
    provider.checked.clear()
    body = {"eventType": kind, "event_id": "22222222-3333-4444-5555-666666666666", "created_at": later(minutes=-1), "data": {"buyer": {"email": "buyer@example.org"}, "amount": 199}}
    assert (await hook(client, body)).status == 204
    assert provider.checked == []
    assert (await rows(factory))[0].state == "active"
    stored = [item for item in await rows(factory, BillingEvent) if item.kind == kind]
    assert len(stored) == 1 and "buyer@example.org" not in json.dumps(stored[0].payload)


async def test_cancelling_uses_the_first_invoice_and_the_saved_email_and_keeps_the_paid_period(setup):
    client, provider, factory = setup
    assert (await client.post("/render/billing/cancel", headers=ONE)).status == 409
    await pay(client)
    provider.invoices[FIRST] = invoice(expires=later(days=20))
    await hook(client, event("payment.success"))
    end = later(days=20)
    provider.invoices[FIRST] = invoice(sub="CANCELLED", expires=end)
    response = await client.post("/render/billing/cancel", headers=ONE)
    assert response.status == 200
    shown = (await response.json())["subscription"]
    assert shown["state"] == "cancelled" and shown["access"] is True
    assert provider.cancelled == [(FIRST, "buyer@example.org")]
    assert (await client.post("/render/billing/cancel", headers=ONE)).status == 409
    assert (await client.post("/render/billing/cancel", headers={"X-Test-Owner": "2"})).status == 409
    assert (await client.post("/render/billing/cancel")).status == 401


async def test_a_cancel_the_provider_cannot_take_changes_nothing(setup):
    client, provider, factory = setup
    await pay(client)
    provider.invoices[FIRST] = invoice()
    await hook(client, event("payment.success"))

    async def broken(invoice_id, email):
        raise LavaError("lava.top connection failed")

    provider.cancel = broken
    response = await client.post("/render/billing/cancel", headers=ONE)
    assert response.status == 502 and await response.json() == {"error": "payment provider unavailable"}
    assert (await rows(factory))[0].state == "active"


async def test_a_paid_subscription_blocks_a_second_one_until_it_ends(setup):
    client, provider, factory = setup
    await pay(client)
    provider.invoices[FIRST] = invoice()
    await hook(client, event("payment.success"))
    response = await pay(client)
    assert response.status == 409 and (await response.json())["error"] == "exists"
    async with factory() as session:
        row = (await session.execute(select(BillingSubscription))).scalars().one()
        row.paid_until = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        row.checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
    shown = (await (await client.get("/render/billing/status", headers=ONE)).json())["subscription"]
    assert shown["state"] == "expired" and shown["access"] is False


STATUS = "/render/billing/status"
DAY = 86400


async def subscribed(client, provider, plan=PLAN, days=30):
    assert (await pay(client, plan=plan)).status == 201
    provider.invoices[FIRST] = invoice(expires=later(days=days))
    assert (await hook(client, event("payment.success"))).status == 204


async def shown_to(client, headers=ONE):
    return (await (await client.get(STATUS, headers=headers)).json())["subscription"]


async def paid_change(client, provider, factory, plan, days=30):
    row = next(row for row in await rows(factory) if row.plan_key == plan)
    provider.invoices[row.invoice_id] = {**invoice(expires=later(days=days)), "id": row.invoice_id}
    assert (await hook(client, event("payment.success", contract=row.invoice_id))).status == 204
    return row.id


async def test_a_higher_tier_replaces_the_held_one_and_carries_its_unused_days_over(setup):
    client, provider, factory = setup
    await subscribed(client, provider, days=20)
    plain = await pay(client, plan=PRO)
    assert plain.status == 409 and (await plain.json())["error"] == "exists"
    response = await pay(client, plan=PRO, change=True)
    assert response.status == 201
    asked = (await response.json())["subscription"]
    assert (asked["state"], asked["plan"]["id"], asked["payment_url"]) == ("pending", PRO, "https://pay.example/checkout/2")
    held = await shown_to(client)
    assert (held["plan"]["id"], held["state"], held["access"]) == (PLAN, "active", True)
    assert (held["change"]["state"], held["change"]["plan"]["id"], held["change"]["payment_url"]) == ("pending", PRO, "https://pay.example/checkout/2")
    assert provider.cancelled == [] and held["carried_seconds"] == 0
    await paid_change(client, provider, factory, PRO)
    now = await shown_to(client)
    assert (now["plan"]["id"], now["state"], now["access"], now["change"]) == (PRO, "active", True, None)
    assert provider.cancelled == [(FIRST, "buyer@example.org")]
    assert abs(now["carried_seconds"] - 10 * DAY) < 300
    old = next(row for row in await rows(factory) if row.plan_key == PLAN)
    assert old.state == "replaced" and old.cancelled_at is not None
    assert (await shown_to(client))["carried_seconds"] == now["carried_seconds"]
    assert provider.cancelled == [(FIRST, "buyer@example.org")]


async def test_a_lower_tier_and_the_plan_already_held_are_not_a_change(setup):
    client, provider, factory = setup
    await subscribed(client, provider, plan=PRO)
    same = await pay(client, plan=PRO, change=True)
    assert same.status == 409 and (await same.json())["error"] == "same"
    lower = await pay(client, plan=PLAN, change=True)
    body = await lower.json()
    assert lower.status == 409 and body["error"] == "lower" and body["subscription"]["plan"]["id"] == PRO
    assert len(provider.created) == 1 and len(await rows(factory)) == 1 and provider.cancelled == []


@pytest.mark.parametrize("wanted,rate", [
    (f"{OFFER}:RUB:PERIOD_90_DAYS", (199 / 30) / (540 / 90)),
    (f"{OFFER}:USD:MONTHLY", 1.0),
    (f"{HIGH}:USD:MONTHLY", 0.5),
])
async def test_another_period_or_currency_is_a_change_weighed_by_the_price_of_a_day(setup, wanted, rate):
    client, provider, factory = setup
    await subscribed(client, provider, days=30)
    assert (await pay(client, plan=wanted, change=True)).status == 201
    await paid_change(client, provider, factory, wanted)
    now = await shown_to(client)
    assert now["plan"]["id"] == wanted and now["state"] == "active"
    assert abs(now["carried_seconds"] - 30 * DAY * rate) < 300
    assert provider.cancelled == [(FIRST, "buyer@example.org")]


async def test_an_unpaid_change_leaves_the_held_subscription_alone_and_blocks_another_try(setup):
    client, provider, factory = setup
    await subscribed(client, provider)
    assert (await pay(client, plan=PRO, change=True)).status == 201
    again = await pay(client, plan=f"{HIGH}:USD:MONTHLY", change=True)
    body = await again.json()
    assert again.status == 409 and body["error"] == "busy" and body["subscription"]["plan"]["id"] == PRO
    assert len(provider.created) == 2 and provider.cancelled == []
    async with factory() as session:
        row = (await session.execute(select(BillingSubscription).where(BillingSubscription.plan_key == PRO))).scalars().one()
        row.created_at = row.created_at - timedelta(hours=1)
        await session.commit()
    held = await shown_to(client)
    assert (held["plan"]["id"], held["state"], held["change"]) == (PLAN, "active", None)
    assert next(row for row in await rows(factory) if row.plan_key == PLAN).state == "active"


async def test_a_change_the_provider_cannot_cancel_yet_is_shown_and_finished_later(setup):
    client, provider, factory = setup
    await subscribed(client, provider, days=20)
    await pay(client, plan=PRO, change=True)
    cancel = provider.cancel

    async def broken(invoice_id, email):
        raise LavaError("lava.top connection failed")

    provider.cancel = broken
    await paid_change(client, provider, factory, PRO)
    now = await shown_to(client)
    assert (now["plan"]["id"], now["state"]) == (PRO, "active")
    carried = now["carried_seconds"]
    assert abs(carried - 10 * DAY) < 300
    assert next(row for row in await rows(factory) if row.plan_key == PLAN).state == "active"
    provider.cancel = cancel
    later_on = await shown_to(client)
    assert later_on["plan"]["id"] == PRO and later_on["carried_seconds"] == carried
    assert provider.cancelled == [(FIRST, "buyer@example.org")]
    assert next(row for row in await rows(factory) if row.plan_key == PLAN).state == "replaced"
    await shown_to(client)
    assert provider.cancelled == [(FIRST, "buyer@example.org")]


async def test_a_cancelled_subscription_that_still_runs_is_carried_over_without_another_cancel(setup):
    client, provider, factory = setup
    await subscribed(client, provider, days=20)
    provider.invoices[FIRST] = invoice(sub="CANCELLED", expires=later(days=20))
    assert (await client.post("/render/billing/cancel", headers=ONE)).status == 200
    assert (await pay(client, plan=PRO, change=True)).status == 201
    await paid_change(client, provider, factory, PRO)
    now = await shown_to(client)
    assert now["plan"]["id"] == PRO and abs(now["carried_seconds"] - 10 * DAY) < 300
    assert provider.cancelled == [(FIRST, "buyer@example.org")]
    assert next(row for row in await rows(factory) if row.plan_key == PLAN).state == "replaced"


def held_row(at, **fields):
    base = dict(state="cancelled", paid_until=at - timedelta(days=1), bonus_seconds=3 * DAY, created_at=at - timedelta(days=40), plan_key=PLAN, title="Dossier", name="Plus", amount="199", currency="RUB", periodicity="MONTHLY")
    return BillingSubscription(**{**base, **fields})


@pytest.mark.parametrize("state", ["cancelled", "active", "expired", "failed"])
def test_carried_days_keep_access_after_the_paid_period_and_then_run_out(state):
    at = datetime(2026, 10, 9, 12, 0)
    row = held_row(at, state=state)
    assert subscriptions.has_access(row, at)
    shown = subscriptions.view(row, at)
    assert (shown["state"], shown["access"], shown["paid_until"], shown["carried_seconds"]) == ("cancelled", True, "2026-10-11T12:00:00Z", 3 * DAY)
    after = at + timedelta(days=2, seconds=1)
    assert not subscriptions.has_access(row, after)
    assert subscriptions.view(row, after)["state"] == ("expired" if state in ("cancelled", "active") else state)


def test_a_renewing_subscription_shows_its_next_payment_and_not_the_carried_end():
    at = datetime(2026, 10, 9, 12, 0)
    row = held_row(at, state="active", paid_until=at + timedelta(days=5))
    shown = subscriptions.view(row, at)
    assert (shown["state"], shown["paid_until"], shown["carried_seconds"]) == ("active", "2026-10-14T12:00:00Z", 3 * DAY)
    assert not subscriptions.has_access(held_row(at, state="failed", bonus_seconds=0), at)
    assert not subscriptions.has_access(held_row(at, state="pending"), at)


def test_a_replaced_subscription_stays_replaced_whatever_the_provider_says():
    at = datetime(2026, 10, 9, 12, 0)
    row = held_row(at, state="replaced", paid_until=at + timedelta(days=9))
    subscriptions.apply_invoice(row, {"status": "COMPLETED", "subscriptionStatus": "ACTIVE", "subscriptionDetails": {"expiredAt": "2999-01-01T00:00:00Z"}}, at)
    assert row.state == "replaced" and row.checked_at == at
    assert not subscriptions.has_access(row, at) and not subscriptions.blocks(row, at)


async def test_the_change_columns_are_added_to_a_table_made_before_them():
    from sqlalchemy import text

    from db.migrations._utils import existing_columns
    from db.migrations.add_billing_change import run_billing_change_migration

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await run_billing_change_migration(engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE billing_subscriptions (id VARCHAR(32) PRIMARY KEY, state VARCHAR(16) NOT NULL)"))
        await conn.execute(text("INSERT INTO billing_subscriptions (id, state) VALUES ('a', 'active')"))
    await run_billing_change_migration(engine)
    await run_billing_change_migration(engine)
    async with engine.begin() as conn:
        assert {"replaces_id", "credit_rate", "bonus_seconds", "settled_at"} <= await existing_columns(conn, "billing_subscriptions")
        assert (await conn.execute(text("SELECT bonus_seconds, replaces_id FROM billing_subscriptions"))).all() == [(None, None)]
    await engine.dispose()


@pytest.mark.parametrize("invoice_body,start,state", [
    ({"status": "NEW"}, "pending", "pending"),
    ({"status": "IN_PROGRESS"}, "creating", "pending"),
    ({"status": "FAILED"}, "pending", "failed"),
    ({"status": "COMPLETED", "subscriptionStatus": "ACTIVE", "subscriptionDetails": {"expiredAt": "2999-01-01T00:00:00Z"}}, "pending", "active"),
    ({"status": "COMPLETED", "subscriptionStatus": "CANCELLED", "subscriptionDetails": {"expiredAt": "2999-01-01T00:00:00Z", "cancelledAt": "2026-01-01T00:00:00Z"}}, "active", "cancelled"),
    ({"status": "COMPLETED", "subscriptionStatus": "FAILED"}, "active", "failed"),
    ({"status": "COMPLETED", "subscriptionStatus": "ACTIVE", "subscriptionDetails": {"terminatedAt": "2020-01-01T00:00:00Z"}}, "active", "expired"),
    ({"status": "FAILED", "subscriptionStatus": "ACTIVE"}, "active", "active"),
    ({"status": "NEW"}, "active", "active"),
    ({"status": "SOMETHING"}, "pending", "pending"),
    ({}, "pending", "pending"),
])
def test_the_provider_invoice_decides_the_state(invoice_body, start, state):
    at = datetime(2026, 10, 9, 12, 0)
    row = BillingSubscription(state=start, created_at=at - timedelta(minutes=1), paid_until=None, cancelled_at=None)
    subscriptions.apply_invoice(row, invoice_body, at)
    assert row.state == state
    assert row.checked_at == at


def test_an_unpaid_invoice_that_is_old_enough_expires():
    at = datetime(2026, 10, 9, 12, 0)
    row = BillingSubscription(state="pending", created_at=at - timedelta(hours=1))
    subscriptions.apply_invoice(row, {"status": "NEW"}, at)
    assert row.state == "expired"


@pytest.mark.parametrize("value,expected", [
    ("2024-02-05T09:38:27.33277Z", datetime(2024, 2, 5, 9, 38, 27, 332770)),
    ("2024-02-05T12:38:27+03:00", datetime(2024, 2, 5, 9, 38, 27)),
    ("2024-02-05T09:38:27", datetime(2024, 2, 5, 9, 38, 27)),
    ("yesterday", None), (None, None), (5, None),
])
def test_provider_times_become_utc_wall_clock(value, expected):
    assert subscriptions.moment(value) == expected


async def test_a_long_payment_link_is_kept_whole(setup):
    client, provider, factory = setup
    link = "https://pay.example/checkout/" + "t" * 1500

    async def create(offer, email, ref):
        return Checkout(FIRST, link)

    provider.create = create
    response = await pay(client)
    assert response.status == 201 and (await response.json())["subscription"]["payment_url"] == link
    assert BillingSubscription.__table__.c.payment_url.type.__class__.__name__ == "Text"


async def test_a_fault_of_our_own_is_answered_as_ours_and_logged(setup, caplog):
    client, provider, factory = setup

    async def create(offer, email, ref):
        raise RuntimeError("storage broke")

    provider.create = create
    response = await pay(client)
    assert response.status == 500 and await response.json() == {"error": "checkout failed"}
    assert "broke on our side" in caplog.text
