from sqlalchemy import text

from db.migrations._utils import add_column, table_exists
from db.models.billing import BillingSubscription

_COLUMNS = ("replaces_id", "credit_rate", "bonus_seconds", "settled_at")

async def run_billing_change_migration(engine) -> None:
    async with engine.begin() as conn:
        if not await table_exists(conn, BillingSubscription.__tablename__):
            return
        for name in _COLUMNS:
            await add_column(conn, BillingSubscription.__table__.c[name])
        if conn.dialect.name == "postgresql":
            await conn.execute(text('ALTER TABLE "billing_subscriptions" ALTER COLUMN "payment_url" TYPE TEXT'))
