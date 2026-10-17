from db.migrations._utils import add_column, table_exists
from db.models.player import Player

async def run_command_ruleset_migration(engine) -> None:
    async with engine.begin() as conn:
        if await table_exists(conn, Player.__tablename__):
            await add_column(conn, Player.__table__.c["command_ruleset"])
