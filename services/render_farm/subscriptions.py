import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import aiohttp
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.database import AsyncSessionFactory
from db.models.billing import BillingEvent, BillingSubscription
from services.lava_top import ADJUSTMENTS, LavaClient, LavaConfig, LavaError, identifier

from utils.logger import get_logger

log = get_logger("services.render_farm.subscriptions")

UNSURE_FOR = timedelta(minutes=2)
PENDING_FOR = timedelta(minutes=30)
POLL_EVERY = timedelta(seconds=4)
WATCH_EVERY = timedelta(minutes=1)
RECHECK_EVERY = timedelta(minutes=30)
WAITING = ("creating", "unknown", "pending")
PAID = ("active", "cancelled")
LAPSED = ("failed", "expired")
DAYS = {"MONTHLY": 30, "PERIOD_90_DAYS": 90, "PERIOD_180_DAYS": 180, "PERIOD_YEAR": 365}
MOST_CARRIED = timedelta(days=1095)
MOST_RATE = 50.0


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


def until(row) -> datetime | None:
    if row.paid_until is None:
        return None
    return row.paid_until + timedelta(seconds=row.bonus_seconds or 0)


def has_access(row, at: datetime) -> bool:
    end = until(row)
    if row.state == "active":
        return end is None or end > at
    if row.state == "cancelled" or (row.state in LAPSED and row.bonus_seconds):
        return end is not None and end > at
    return False


def view(row, at: datetime, change: dict | None = None) -> dict:
    access = has_access(row, at)
    renewing = row.state == "active" and (row.paid_until is None or row.paid_until > at)
    if access and not renewing:
        state = "cancelled"
    elif row.state in PAID and not access:
        state = "expired"
    else:
        state = row.state
    return {
        "state": state,
        "access": access,
        "plan": {"id": row.plan_key, "title": row.title, "name": row.name, "amount": row.amount, "currency": row.currency, "periodicity": row.periodicity},
        "paid_until": stamp(row.paid_until if renewing else until(row)),
        "carried_seconds": row.bonus_seconds or 0,
        "cancelled_at": stamp(row.cancelled_at),
        "created_at": stamp(row.created_at),
        "payment_url": row.payment_url if state == "pending" else None,
        "change": change,
    }


def per_day(amount, periodicity) -> Decimal | None:
    days = DAYS.get(periodicity)
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return None
    if days is None or not value.is_finite() or value <= 0:
        return None
    return value / days


def listed(prices, offer_id: str, currency: str, periodicity: str) -> Decimal | None:
    found = next((price for price in prices if price.offer_id == offer_id and price.currency == currency and price.periodicity == periodicity), None)
    return None if found is None else per_day(found.amount, found.periodicity)


def weigh(row, price, prices) -> tuple[str, float | None]:
    held_offer = row.plan_key.split(":")[0]
    wanted = per_day(price.amount, price.periodicity)
    paid = per_day(row.amount, row.periodicity)
    if wanted is None or paid is None:
        return "unknown", None
    if row.currency != price.currency:
        there, here = listed(prices, held_offer, price.currency, row.periodicity), listed(prices, held_offer, row.currency, row.periodicity)
        if there is None or here is None:
            return "unknown", None
        paid = paid * there / here
    rate = min(float(paid / wanted), MOST_RATE)
    if held_offer == price.offer_id:
        return "same", rate
    pairs = [(price.currency, price.periodicity), (row.currency, row.periodicity)]
    pairs += [(other.currency, other.periodicity) for other in prices if other.offer_id == price.offer_id]
    for currency, periodicity in pairs:
        old, new = listed(prices, held_offer, currency, periodicity), listed(prices, price.offer_id, currency, periodicity)
        if old is not None and new is not None:
            return ("lower" if new < old else "higher"), rate
    return "unknown", None


def apply_invoice(row, invoice: dict, at: datetime) -> None:
    status, kind = invoice.get("status"), invoice.get("subscriptionStatus")
    details = invoice.get("subscriptionDetails") if isinstance(invoice.get("subscriptionDetails"), dict) else {}
    expires, cancelled, ended = moment(details.get("expiredAt")), moment(details.get("cancelledAt")), moment(details.get("terminatedAt"))
    row.checked_at = at
    if row.state == "replaced":
        return
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


async def settle(rows, provider, at: datetime) -> None:
    known = {row.id: row for row in rows}
    for row in rows:
        if row.replaces_id is None or row.state not in PAID:
            continue
        old = known.get(row.replaces_id)
        if row.settled_at is None:
            end = until(old) if old is not None and has_access(old, at) else None
            left = max((end - at).total_seconds(), 0.0) if end is not None else 0.0
            row.bonus_seconds = int(min(left * (row.credit_rate or 0.0), MOST_CARRIED.total_seconds()))
            row.settled_at = at
        if old is None or old.state == "replaced":
            continue
        if old.state == "active" and old.invoice_id:
            try:
                await provider.cancel(old.invoice_id, old.email)
            except LavaError:
                log.error("lava.top subscription %s gave way to checkout %s but could not be cancelled", old.invoice_id, row.id)
                continue
        old.state, old.cancelled_at = "replaced", old.cancelled_at or at


async def start(player_id: int, price, email: str, provider, change: bool = False, prices=()) -> dict:
    async with lock_of(player_id):
        async with AsyncSessionFactory() as session:
            at = now()
            rows = await rows_of(session, player_id)
            held = next((row for row in rows if has_access(row, at)), None)
            waiting = next((row for row in rows if not has_access(row, at) and blocks(row, at)), None)
            if held is not None and not change:
                raise Refused("exists", view(held, at))
            if waiting is not None:
                raise Refused("busy", view(waiting, at))
            rate = None
            if held is not None:
                if held.plan_key == price.key:
                    raise Refused("same", view(held, at))
                kind, rate = weigh(held, price, prices)
                if kind in ("lower", "unknown"):
                    raise Refused(kind, view(held, at))
            row = BillingSubscription(
                id=uuid.uuid4().hex, player_id=player_id, plan_key=price.key, title=price.title, name=price.name,
                amount=price.amount, currency=price.currency, periodicity=price.periodicity, email=email,
                state="creating", created_at=at, updated_at=at,
                replaces_id=None if held is None else held.id, credit_rate=rate, bonus_seconds=0,
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
        first = sorted(rows, key=lambda row: rank(row, at))[0]
        coming = next((row for row in rows if row is not first and row.replaces_id and row.state in WAITING and blocks(row, at)), None)
        for row in (first, coming):
            if row is None:
                continue
            if row.state in ("creating", "unknown") and at - row.created_at >= UNSURE_FOR:
                row.state = "failed"
            elif row.invoice_id and due(row, at):
                try:
                    await refresh(row, provider)
                except LavaError:
                    log.warning("lava.top invoice %s could not be checked", row.invoice_id)
        await settle(rows, provider, at)
        await session.commit()
        first = sorted(rows, key=lambda row: rank(row, at))[0]
        coming = next((row for row in rows if row.replaces_id == first.id and row.state == "pending" and blocks(row, at)), None)
        return view(first, at, None if coming is None else view(coming, at))


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
            await settle(await rows_of(session, row.player_id), provider, now())
            await session.commit()
    except LavaError:
        log.warning("lava.top %s %s was stored but the invoice could not be checked", event.kind, event.deduplication_key)
    return fresh
