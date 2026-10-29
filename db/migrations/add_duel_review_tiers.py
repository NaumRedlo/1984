from db.migrations._utils import add_column, table_exists
from db.models.duel_catalog import DuelMapAssignment

async def run_duel_review_tiers_migration(engine) -> None:
    async with engine.begin() as conn:
        if await table_exists(conn, DuelMapAssignment.__tablename__):
            await add_column(conn, DuelMapAssignment.__table__.c["tier"])
            await add_column(conn, DuelMapAssignment.__table__.c["reasons"])
