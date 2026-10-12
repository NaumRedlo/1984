# Billing through lava.top

`services/lava_top.py` provides an opt-in API adapter for subscription checkout,
invoice and subscription lookup, cancellation, authenticated webhook parsing,
and complete paginated reads of products and subscriptions.
It follows the [lava.top API](https://developers.lava.top/en) and
[OpenAPI schema](https://gate.lava.top/docs/documentation.yaml).
The authenticated `GET /render/billing/plans` endpoint returns selected tariffs
from lava.top, cached for 60 seconds. The same module registers the rest of the
billing routes, all keyed to the signed-in Dossier account's `Player`:
`GET /render/billing/status` (the account's subscription, checked against
lava.top at most every few seconds while a payment is pending),
`POST /render/billing/checkout` (`{"plan", "email"}`, the plan is resolved from the
catalogue on the server and a payment link comes back), `POST /render/billing/cancel`,
and `POST /render/billing/webhook` (authenticated with `LAVA_TOP_WEBHOOK_KEY`, not
with a Dossier token). State lives in `billing_subscriptions` and `billing_events`
(`services/render_farm/subscriptions.py`). Nothing in the bot is gated by it yet:
the status only reports `state` and `access`.

A held subscription refuses a second checkout with 409 `exists` unless the body
carries `"change": true`. A change is allowed to a higher tier, or to another period
or currency of the same tier; the plan already held answers `same`, a lower tier
answers `lower` (cancel and subscribe again after the paid period), and a tier that
cannot be compared in the catalogue answers `unknown`. Tiers are compared by the
price of a day at the same currency and period. While the new payment is pending the
status keeps showing the held subscription with the pending one under `change`.
Once it is paid, the server cancels the old subscription at lava.top, marks it
`replaced`, and carries its unused time over: the seconds left, multiplied by the old
price of a day over the new one, are stored in `bonus_seconds` and reported as
`carried_seconds`. Carried time is a tail after the paid period, so it counts once
the new subscription stops renewing; `paid_until` is the next payment while it
renews and the end of access afterwards. A cancel that lava.top does not take is
tried again on every later status check and webhook. The columns for this are added
by `db/migrations/add_billing_change.py` on SQLite and PostgreSQL alike.

A subscriber badge comes with the status as `badge`: the held (or last held) tier,
the title worn, every tier once bought, and a stage from 1 to 4. The stage follows
only the time of paid subscription that was actually lived, on any tier and across
breaks: 90, 365 and 730 days open stages 2, 3 and 4; a year bought ahead counts day
by day, and time stops when the subscription ends. The badge is never taken away.
`POST /render/billing/badge` (`{"show_badge", "show_title", "title"}`) hides either
mark or picks the title among bought tiers; `GET /render/billing/badges?players=1,2`
tells signed-in clients what other players chose to show. Choices live in
`billing_badges`.

`LavaConfig.from_env()` reads `LAVA_TOP_ENABLED` (false by default),
`LAVA_TOP_API_KEY` (the outgoing key from lava.top), and
`LAVA_TOP_WEBHOOK_KEY` (a separate secret generated on our server, at most
80 ASCII characters). Configure the latter as the webhook's `X-Api-Key`.
The webhook key may be omitted while only reading the catalogue; incoming
webhooks are rejected until it is configured.
Both secrets belong on the server, never in Dossier or a browser bundle.
`LAVA_TOP_PRODUCT_IDS` selects the subscription product UUIDs that belong to
Dossier, separated by commas. No selection means the catalogue stays unavailable.
Names, prices, currencies, and billing periods are read from lava.top, including
hidden products and periods longer than a month. A failed refresh returns 503
rather than serving old prices. Subscription lists are never exposed to clients.

To check the account before enabling billing, run `python -m scripts.lava_check`
from the server repository. It reads `LAVA_TOP_API_KEY` from the server environment
or `.env`, or prompts for it without echoing. It performs GET requests only and
prints the available tariffs with product IDs and subscription counts, not buyer
emails or credentials. Compare the count with the creator dashboard: the API
describes the subscription listing as scoped to the supplied API key, so do not
assume that historical purchases through other channels are included.

The points below are what the routes above follow. Checkout carries our own
reference in `clientUtm.utm_content`, so a webhook for an invoice whose creation
answer was lost can still be matched to its buyer.

- Resolve the selected catalogue price to a `SubscriptionOffer` on the server
  when adding checkout. Do not accept arbitrary offer IDs or amounts from clients.
- Resolve the signed-in Dossier account to `Player.id`, and persist ownership
  of each checkout before granting access. Buyer email is not account identity.
- Persist events using `WebhookEvent.deduplication_key` under a unique constraint;
  reconcile provider state before applying access changes. Delivery may be out of
  order. Cancellation keeps the paid-through date; it is not an immediate refund.
- Refund and chargeback payloads do not provide an invoice ID in the documented
  examples. Reconcile these explicitly; do not revoke access by email alone.
- Acknowledge authenticated unknown events with 2xx. Store known events durably
  before acknowledging them. Returning to a success URL never grants access.
- Do not blindly retry checkout creation after a timeout: the provider may have
  created the invoice. The adapter intentionally does not retry mutations.

The adapter tests use a local fake provider and make no real payment requests.
