from db.migrations._utils import add_column, table_exists
from db.models.billing import BillingBadge
from db.models.player import Player
from db.models.user import User

async def run_support_level_migration(engine) -> None:
    async with engine.begin() as conn:
        for model in (User, Player):
            if await table_exists(conn, model.__tablename__):
                await add_column(conn, model.__table__.c["support_level"])
        if await table_exists(conn, BillingBadge.__tablename__):
            await add_column(conn, BillingBadge.__table__.c["tint_cards"])
