from db.migrations._utils import add_column, table_exists
from db.models.map_attempt import UserMapAttempt

async def run_attempt_ruleset_migration(engine) -> None:
    async with engine.begin() as conn:
        if await table_exists(conn, UserMapAttempt.__tablename__):
            await add_column(conn, UserMapAttempt.__table__.c["ruleset"])
