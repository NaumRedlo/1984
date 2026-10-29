from db.migrations._utils import add_column, table_exists
from db.models.duel_catalog import DuelMapRevision

async def run_duel_map_tags_migration(engine) -> None:
    async with engine.begin() as conn:
        if await table_exists(conn, DuelMapRevision.__tablename__):
            await add_column(conn, DuelMapRevision.__table__.c["tags"])
