from db.migrations._utils import add_column, table_exists
from db.models.user import User

TEAM_COLUMNS = ("team_id", "team_name", "team_tag", "team_flag_url")

async def run_teams_migration(engine) -> None:
    async with engine.begin() as conn:
        if await table_exists(conn, User.__tablename__):
            for name in TEAM_COLUMNS:
                await add_column(conn, User.__table__.c[name])
