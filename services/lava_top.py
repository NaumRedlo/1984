import asyncio
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

import aiohttp


API_URL = "https://gate.lava.top"
MAX_BODY = 256 * 1024
PERIODS = {"MONTHLY", "PERIOD_90_DAYS", "PERIOD_180_DAYS", "PERIOD_YEAR"}
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
            if not self.api_key or not self.webhook_key or self.api_key == self.webhook_key:
                raise ValueError("Two distinct lava.top keys are required")
            for key in (self.api_key, self.webhook_key):
                if not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
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
        self, offer: SubscriptionOffer, email: str, *, return_url: str | None = None,
    ) -> Checkout:
        payload = {
            "offerId": offer.offer_id, "email": email_address(email),
            "currency": offer.currency, "periodicity": offer.periodicity,
        }
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
    if not config.enabled or not supplied_key or not hmac.compare_digest(
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
