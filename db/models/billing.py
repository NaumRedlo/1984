from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text

from db.database import Base


class BillingSubscription(Base):
    __tablename__ = "billing_subscriptions"

    id = Column(String(32), primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    invoice_id = Column(String(36), unique=True, nullable=True)
    plan_key = Column(String(120), nullable=False)
    title = Column(String(200), nullable=False, default="")
    name = Column(String(200), nullable=False, default="")
    amount = Column(String(32), nullable=False)
    currency = Column(String(3), nullable=False)
    periodicity = Column(String(20), nullable=False)
    email = Column(String(254), nullable=False)
    payment_url = Column(Text, nullable=True)
    state = Column(String(16), nullable=False, index=True)
    paid_until = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    checked_at = Column(DateTime, nullable=True)
    replaces_id = Column(String(32), nullable=True)
    credit_rate = Column(Float, nullable=True)
    bonus_seconds = Column(Integer, nullable=True, default=0)
    settled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class BillingEvent(Base):
    __tablename__ = "billing_events"

    deduplication_key = Column(String(120), primary_key=True)
    kind = Column(String(64), nullable=False)
    invoice_id = Column(String(36), nullable=True, index=True)
    parent_invoice_id = Column(String(36), nullable=True)
    occurred_at = Column(DateTime, nullable=False)
    received_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    payload = Column(JSON, nullable=False)
