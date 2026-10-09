import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import aiohttp
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.database import AsyncSessionFactory
from db.models.billing import BillingEvent, BillingSubscription
from services.lava_top import ADJUSTMENTS, LavaClient, LavaConfig, LavaError, identifier

log = logging.getLogger(__name__)

UNSURE_FOR = timedelta(minutes=15)
PENDING_FOR = timedelta(minutes=30)
POLL_EVERY = timedelta(seconds=4)
WATCH_EVERY = timedelta(minutes=1)
RECHECK_EVERY = timedelta(minutes=30)
WAITING = ("creating", "unknown", "pending")
PAID = ("active", "cancelled")


class Refused(Exception):
    def __init__(self, reason: str, shown: dict | None = None):
        self.reason = reason
        self.shown = shown
        super().__init__(reason)


class Provider:
    def __init__(self, config: LavaConfig):
        self.config = config

    async def _call(self, call):
        async with aiohttp.ClientSession() as session:
            return await call(LavaClient(session, self.config))

    async def create(self, offer, email, ref):
        return await self._call(lambda client: client.create_subscription(offer, email, ref=ref))

    async def invoice(self, invoice_id):
        return await self._call(lambda client: client.invoice(invoice_id))

    async def cancel(self, invoice_id, email):
        return await self._call(lambda client: client.cancel_subscription(invoice_id, email))


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def moment(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def stamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat() + "Z"


def has_access(row, at: datetime) -> bool:
    if row.state == "active":
        return row.paid_until is None or row.paid_until > at
    if row.state == "cancelled":
        return row.paid_until is not None and row.paid_until > at
    return False


def view(row, at: datetime) -> dict:
    access = has_access(row, at)
    state = "expired" if row.state in PAID and not access else row.state
    return {
        "state": state,
        "access": access,
        "plan": {"id": row.plan_key, "title": row.title, "name": row.name, "amount": row.amount, "currency": row.currency, "periodicity": row.periodicity},
        "paid_until": stamp(row.paid_until),
        "cancelled_at": stamp(row.cancelled_at),
        "created_at": stamp(row.created_at),
        "payment_url": row.payment_url if state == "pending" else None,
    }


def apply_invoice(row, invoice: dict, at: datetime) -> None:
    status, kind = invoice.get("status"), invoice.get("subscriptionStatus")
    details = invoice.get("subscriptionDetails") if isinstance(invoice.get("subscriptionDetails"), dict) else {}
    expires, cancelled, ended = moment(details.get("expiredAt")), moment(details.get("cancelledAt")), moment(details.get("terminatedAt"))
    row.checked_at = at
    if status in ("NEW", "IN_PROGRESS"):
        if row.state in WAITING:
            row.state = "expired" if at - row.created_at > PENDING_FOR else "pending"
        return
    if status == "FAILED" and kind not in ("ACTIVE", "CANCELLED"):
        if row.state not in PAID:
            row.state = "failed"
        return
    if status != "COMPLETED":
        return
    if kind == "CANCELLED":
        row.state = "cancelled"
        row.cancelled_at = cancelled or row.cancelled_at or at
    elif kind == "FAILED":
        row.state = "failed"
    else:
        row.state = "active"
    if expires is not None:
        row.paid_until = expires
    if ended is not None and ended <= at:
        row.state = "expired"


async def refresh(row, provider, hint: datetime | None = None) -> None:
    if row.invoice_id is None:
        return
    apply_invoice(row, await provider.invoice(row.invoice_id), now())
    if row.state == "cancelled" and row.paid_until is None and hint is not None:
        row.paid_until = hint


def due(row, at: datetime) -> bool:
    waited = None if row.checked_at is None else at - row.checked_at
    if row.state == "pending":
        return waited is None or waited > POLL_EVERY
    if row.state in PAID:
        if row.paid_until is not None and row.paid_until <= at:
            return waited is None or waited > WATCH_EVERY
        return waited is None or waited > RECHECK_EVERY
    return False


def blocks(row, at: datetime) -> bool:
    if has_access(row, at):
        return True
    if row.state in ("creating", "unknown"):
        return at - row.created_at < UNSURE_FOR
    return row.state == "pending" and at - row.created_at < PENDING_FOR


def rank(row, at: datetime) -> int:
    if has_access(row, at):
        return 0
    return 1 if blocks(row, at) else 2


async def rows_of(session, player_id: int) -> list:
    found = await session.execute(select(BillingSubscription).where(BillingSubscription.player_id == player_id).order_by(BillingSubscription.created_at.desc(), BillingSubscription.id))
    return list(found.scalars().all())


_locks: dict[int, asyncio.Lock] = {}


def lock_of(player_id: int) -> asyncio.Lock:
    return _locks.setdefault(player_id, asyncio.Lock())


async def start(player_id: int, price, email: str, provider) -> dict:
    async with lock_of(player_id):
        async with AsyncSessionFactory() as session:
            at = now()
            rows = await rows_of(session, player_id)
            for row in sorted(rows, key=lambda row: rank(row, at)):
                if blocks(row, at):
                    raise Refused("exists" if has_access(row, at) else "busy", view(row, at))
            row = BillingSubscription(
                id=uuid.uuid4().hex, player_id=player_id, plan_key=price.key, title=price.title, name=price.name,
                amount=price.amount, currency=price.currency, periodicity=price.periodicity, email=email,
                state="creating", created_at=at, updated_at=at,
            )
            session.add(row)
            await session.commit()
            try:
                checkout = await provider.create(price.offer(), email, row.id)
            except LavaError as error:
                row.state = "failed" if error.status is not None and 400 <= error.status < 500 else "unknown"
                await session.commit()
                raise
            row.invoice_id, row.payment_url, row.state = checkout.invoice_id, checkout.payment_url, "pending"
            try:
                await session.commit()
            except Exception:
                log.error("lava.top invoice %s was created but could not be saved for checkout %s", checkout.invoice_id, row.id)
                raise
            return view(row, at)


async def status(player_id: int, provider) -> dict | None:
    async with AsyncSessionFactory() as session:
        at = now()
        rows = await rows_of(session, player_id)
        if not rows:
            return None
        row = sorted(rows, key=lambda row: rank(row, at))[0]
        if row.state in ("creating", "unknown") and at - row.created_at >= UNSURE_FOR:
            row.state = "failed"
        elif row.invoice_id and due(row, at):
            try:
                await refresh(row, provider)
            except LavaError:
                log.warning("lava.top invoice %s could not be checked", row.invoice_id)
        await session.commit()
        return view(row, at)


async def cancel(player_id: int, provider) -> dict:
    async with AsyncSessionFactory() as session:
        at = now()
        rows = await rows_of(session, player_id)
        row = next((row for row in rows if row.state == "active" and row.invoice_id and has_access(row, at)), None)
        if row is None:
            raise Refused("none")
        await provider.cancel(row.invoice_id, row.email)
        row.state, row.cancelled_at = "cancelled", at
        await session.commit()
        try:
            await refresh(row, provider)
        except LavaError:
            log.warning("lava.top invoice %s could not be checked after cancelling", row.invoice_id)
        await session.commit()
        return view(row, at)


def scrubbed(value):
    if isinstance(value, dict):
        return {key: scrubbed(item) for key, item in value.items() if key not in ("email", "buyer")}
    if isinstance(value, list):
        return [scrubbed(item) for item in value]
    return value


async def owner_of(session, event, provider):
    ids = [value for value in (event.parent_invoice_id, event.invoice_id) if value]
    if not ids:
        return None
    found = await session.execute(select(BillingSubscription).where(BillingSubscription.invoice_id.in_(ids)))
    row = found.scalars().first()
    if row is not None:
        return row
    utm = event.payload.get("clientUtm")
    ref = utm.get("utm_content") if isinstance(utm, dict) else None
    if isinstance(ref, str) and event.invoice_id and event.parent_invoice_id is None and event.kind in ("payment.success", "payment.failed"):
        row = await session.get(BillingSubscription, ref[:32])
        if row is not None and row.invoice_id is None:
            row.invoice_id = event.invoice_id
            return row
    try:
        invoice = await provider.invoice(event.invoice_id)
    except LavaError:
        return None
    parent = invoice.get("parentInvoice")
    try:
        root = identifier(parent.get("id")) if isinstance(parent, dict) and parent.get("id") else None
    except ValueError:
        return None
    if root is None:
        return None
    found = await session.execute(select(BillingSubscription).where(BillingSubscription.invoice_id == root))
    return found.scalars().first()


async def receive(event, provider) -> bool:
    fresh = True
    async with AsyncSessionFactory() as session:
        session.add(BillingEvent(
            deduplication_key=event.deduplication_key, kind=event.kind, invoice_id=event.invoice_id,
            parent_invoice_id=event.parent_invoice_id, occurred_at=naive(event.occurred_at),
            received_at=now(), payload=scrubbed(event.payload),
        ))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            fresh = False
    if event.kind in ADJUSTMENTS:
        log.warning("lava.top %s %s needs a manual look: it names no invoice", event.kind, event.deduplication_key)
        return fresh
    try:
        async with AsyncSessionFactory() as session:
            row = await owner_of(session, event, provider)
            if row is None:
                log.warning("lava.top %s %s belongs to no known checkout", event.kind, event.deduplication_key)
                return fresh
            await refresh(row, provider, naive(event.expires_at))
            await session.commit()
    except LavaError:
        log.warning("lava.top %s %s was stored but the invoice could not be checked", event.kind, event.deduplication_key)
    return fresh
