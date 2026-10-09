import asyncio
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import aiohttp


API_URL = "https://gate.lava.top"
MAX_BODY = 256 * 1024
PERIODS = {"MONTHLY", "PERIOD_90_DAYS", "PERIOD_180_DAYS", "PERIOD_YEAR"}
MAX_PAGES = 200
PAYMENTS = {
    "payment.success", "payment.failed",
    "subscription.recurring.payment.success", "subscription.recurring.payment.failed",
    "subscription.cancelled",
}
ADJUSTMENTS = {"refund.success", "chargeback.initiated"}


class LavaError(Exception):
    def __init__(self, reason: str, status: int | None = None):
        self.status = status
        super().__init__(reason)


@dataclass(frozen=True)
class LavaConfig:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    webhook_key: str = field(default="", repr=False)

    def __post_init__(self):
        if self.enabled:
            if not self.api_key:
                raise ValueError("lava.top API key is required")
            if self.api_key == self.webhook_key:
                raise ValueError("lava.top API and webhook keys must be distinct")
            for key in (self.api_key, self.webhook_key):
                if key and (not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key)):
                    raise ValueError("Invalid lava.top key")
            if len(self.webhook_key) > 80:
                raise ValueError("lava.top webhook key exceeds 80 characters")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        enabled = env.get("LAVA_TOP_ENABLED", "false").lower()
        if enabled not in {"true", "false", "1", "0"}:
            raise ValueError("Invalid LAVA_TOP_ENABLED")
        return cls(enabled in {"true", "1"}, env.get("LAVA_TOP_API_KEY", ""), env.get("LAVA_TOP_WEBHOOK_KEY", ""))


def identifier(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a lava.top identifier")
    return str(UUID(value))


def reference(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or not value.isascii() or not value.isalnum():
        raise ValueError("Invalid checkout reference")
    return value


def https_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() for c in value):
        raise ValueError("Invalid payment URL")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Payment URLs must use HTTPS")
    return value


def email_address(value: str) -> str:
    if not isinstance(value, str) or len(value) > 254 or value.count("@") != 1 or any(c.isspace() for c in value):
        raise ValueError("Invalid buyer email")
    if not all(value.split("@")):
        raise ValueError("Invalid buyer email")
    return value


@dataclass(frozen=True)
class SubscriptionOffer:
    offer_id: str
    currency: str
    periodicity: str

    def __post_init__(self):
        identifier(self.offer_id)
        if self.currency not in {"RUB", "USD", "EUR"} or self.periodicity not in PERIODS:
            raise ValueError("Invalid lava.top subscription offer")


@dataclass(frozen=True)
class Checkout:
    invoice_id: str
    payment_url: str | None


@dataclass(frozen=True)
class CataloguePrice:
    product_id: str
    offer_id: str
    title: str
    name: str
    description: str
    currency: str
    periodicity: str
    amount: str

    @property
    def key(self) -> str:
        return f"{self.offer_id}:{self.currency}:{self.periodicity}"

    def offer(self) -> SubscriptionOffer:
        return SubscriptionOffer(self.offer_id, self.currency, self.periodicity)


def catalogue_prices(products: list[dict], product_ids: frozenset[str] | None = None) -> list[CataloguePrice]:
    prices = {}
    try:
        for product in products:
            if product.get("type") != "SUBSCRIPTION":
                continue
            product_id = identifier(product.get("id"))
            if product_ids is not None and product_id not in product_ids:
                continue
            for offer in product.get("offers") or []:
                offer_id = identifier(offer.get("id"))
                for price in offer.get("prices") or []:
                    period = price.get("periodicity")
                    currency = price.get("currency")
                    if period not in PERIODS or currency not in {"RUB", "EUR", "USD"} or price.get("amount") is None:
                        continue
                    amount = Decimal(str(price["amount"]))
                    if not amount.is_finite() or amount < 0:
                        raise ValueError("Invalid price")
                    title, name, description = product.get("title") or "", offer.get("name") or "", offer.get("description") or ""
                    if not all(isinstance(text, str) for text in (title, name, description)):
                        raise ValueError("Invalid catalogue text")
                    row = CataloguePrice(product_id, offer_id, title, name, description, currency, period, format(amount, "f"))
                    if row.key in prices and prices[row.key] != row:
                        raise ValueError("Conflicting prices")
                    prices[row.key] = row
    except (ValueError, InvalidOperation, TypeError, AttributeError):
        raise LavaError("Invalid lava.top catalogue") from None
    return list(prices.values())


def feed_product(item) -> dict:
    if not isinstance(item, dict):
        raise LavaError("Invalid lava.top product")
    if "data" in item:
        if item.get("type") != "PRODUCT" or not isinstance(item["data"], dict):
            raise LavaError("Invalid lava.top product")
        return item["data"]
    kind = item.get("type")
    if not isinstance(kind, str) or kind in {"PRODUCT", "POST"}:
        raise LavaError("Invalid lava.top product")
    return item


class LavaClient:
    def __init__(self, session: aiohttp.ClientSession, config: LavaConfig):
        self.session = session
        self.config = config

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.config.enabled:
            raise LavaError("lava.top is disabled")
        try:
            async with self.session.request(
                method, API_URL + path,
                headers={"X-Api-Key": self.config.api_key, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False, **kwargs,
            ) as response:
                if not 200 <= response.status < 300:
                    raise LavaError("lava.top request failed", response.status)
                if response.status == 204:
                    return {}
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise LavaError("lava.top response is too large")
                try:
                    value = json.loads(body)
                except (ValueError, UnicodeError):
                    raise LavaError("Invalid lava.top response") from None
                if not isinstance(value, dict):
                    raise LavaError("Invalid lava.top response")
                return value
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise LavaError("lava.top connection failed; request outcome may be unknown") from None

    async def create_subscription(
        self, offer: SubscriptionOffer, email: str, *, return_url: str | None = None, ref: str | None = None,
    ) -> Checkout:
        payload = {
            "offerId": offer.offer_id, "email": email_address(email),
            "currency": offer.currency, "periodicity": offer.periodicity,
        }
        if ref is not None:
            payload["clientUtm"] = {"utm_source": "dossier", "utm_content": reference(ref)}
        if return_url is not None:
            for name in ("successful_return_url", "failure_return_url", "cancel_return_url"):
                payload[name] = https_url(return_url)
        result = await self._request("POST", "/api/v3/invoice", json=payload)
        try:
            invoice_id = identifier(result.get("id"))
            url = result.get("paymentUrl")
            if url is not None:
                url = https_url(url)
        except ValueError:
            raise LavaError("Invalid lava.top checkout response") from None
        return Checkout(invoice_id, url)

    async def invoice(self, invoice_id: str) -> dict:
        return await self._request("GET", f"/api/v2/invoices/{identifier(invoice_id)}")

    async def subscription(self, subscription_id: str) -> dict:
        return await self._request("GET", f"/api/v1/subscriptions/{identifier(subscription_id)}")

    async def products(self) -> list[dict]:
        params = {"contentCategories": "PRODUCT", "productTypes": "SUBSCRIPTION", "showAllSubscriptionPeriods": "true", "feedVisibility": "ALL"}
        products = {}
        cursors = set()
        for _ in range(MAX_PAGES):
            page = await self._request("GET", "/api/v2/products", params=params)
            items = page.get("items")
            if not isinstance(items, list):
                raise LavaError("Invalid lava.top product page")
            for item in items:
                product = feed_product(item)
                try:
                    product_id = identifier(product.get("id"))
                except ValueError:
                    raise LavaError("Invalid lava.top product identifier") from None
                if product_id in products and products[product_id] != product:
                    raise LavaError("lava.top catalogue changed during pagination; try again")
                products[product_id] = product
            link = page.get("nextPage")
            if link is None:
                return list(products.values())
            try:
                if not isinstance(link, str):
                    raise ValueError("Invalid cursor")
                parsed, origin = urlsplit(link), urlsplit(API_URL)
                if (parsed.scheme, parsed.netloc, parsed.path) != (origin.scheme, origin.netloc, "/api/v2/products") or parsed.fragment or parsed.username:
                    raise ValueError("Invalid cursor origin")
                values = parse_qs(parsed.query).get("beforeCreatedAt", [])
                if len(values) != 1 or values[0] in cursors:
                    raise ValueError("Repeated or missing cursor")
                timestamp(values[0])
                params["beforeCreatedAt"] = values[0]
                cursors.add(values[0])
            except (ValueError, TypeError):
                raise LavaError("Invalid lava.top catalogue cursor") from None
        raise LavaError("lava.top catalogue exceeds pagination limit")

    async def subscriptions(self) -> list[dict]:
        rows = {}
        for number in range(1, MAX_PAGES + 1):
            params = [("page", str(number)), ("size", "50")]
            params.extend(("invoiceStatuses", status) for status in ("NEW", "IN_PROGRESS", "COMPLETED", "FAILED"))
            page = await self._request("GET", "/api/v1/subscriptions", params=params)
            items, pages = page.get("items"), page.get("pages")
            if not isinstance(items, list) or type(pages) is not int or pages < 0 or type(page.get("page")) is not int or page["page"] != number:
                raise LavaError("Invalid lava.top subscription page")
            if pages > MAX_PAGES or (pages == 0 and items) or (number > pages and number > 1):
                raise LavaError("Invalid lava.top subscription pagination")
            if number < pages and not items:
                raise LavaError("Incomplete lava.top subscription page")
            for row in items:
                try:
                    key = identifier(row.get("id"))
                except (ValueError, AttributeError):
                    raise LavaError("Invalid lava.top subscription identifier") from None
                if key in rows:
                    raise LavaError("lava.top subscriptions changed during pagination; try again")
                rows[key] = row
            if number >= pages:
                if type(page.get("total")) is not int or page["total"] != len(rows):
                    raise LavaError("Incomplete lava.top subscription snapshot")
                return list(rows.values())
        raise LavaError("lava.top subscriptions exceed pagination limit")

    async def cancel_subscription(self, parent_invoice_id: str, email: str) -> None:
        await self._request("DELETE", "/api/v1/subscriptions", params={
            "contractId": identifier(parent_invoice_id), "email": email_address(email),
        })


@dataclass(frozen=True)
class WebhookEvent:
    kind: str
    deduplication_key: str
    occurred_at: datetime
    invoice_id: str | None
    parent_invoice_id: str | None
    expires_at: datetime | None
    payload: dict = field(repr=False)


def timestamp(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Missing lava.top event timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("lava.top event timestamp must include timezone")
    return parsed


def webhook_event(body: bytes, supplied_key: str | None, config: LavaConfig) -> WebhookEvent | None:
    if not config.enabled or not config.webhook_key or not supplied_key or not hmac.compare_digest(
        supplied_key.encode("utf-8"), config.webhook_key.encode("utf-8"),
    ):
        raise PermissionError("Invalid lava.top webhook credentials")
    if len(body) > MAX_BODY:
        raise ValueError("lava.top webhook is too large")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Invalid lava.top webhook")
    kind = value.get("eventType") or value.get("event_type")
    if not isinstance(kind, str) or not kind:
        raise ValueError("Missing lava.top event type")
    if kind in PAYMENTS:
        invoice_id = identifier(value.get("contractId"))
        parent_id = identifier(value["parentContractId"]) if value.get("parentContractId") else None
        if kind.startswith("subscription.recurring.") and parent_id is None:
            raise ValueError("Missing lava.top parent invoice")
        occurred_at = timestamp(value.get("cancelledAt") if kind == "subscription.cancelled" else value.get("timestamp"))
        expires_at = timestamp(value["willExpireAt"]) if value.get("willExpireAt") else None
        return WebhookEvent(kind, f"{kind}:{invoice_id}", occurred_at, invoice_id, parent_id, expires_at, value)
    if kind in ADJUSTMENTS:
        event_id = identifier(value.get("event_id"))
        if not isinstance(value.get("data"), dict):
            raise ValueError("Invalid lava.top adjustment")
        return WebhookEvent(kind, f"{kind}:{event_id}", timestamp(value.get("created_at")), None, None, None, value)
    return None
