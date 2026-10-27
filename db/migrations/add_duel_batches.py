from db.migrations._utils import add_column, table_exists
from db.models.duel_catalog import DuelTrainingExample

async def run_duel_batches_migration(engine) -> None:
    async with engine.begin() as conn:
        if await table_exists(conn, DuelTrainingExample.__tablename__):
            await add_column(conn, DuelTrainingExample.__table__.c["excluded"])
