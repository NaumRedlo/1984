from sqlalchemy import text

from db.migrations._utils import existing_columns

async def run_share_replays_migration(engine) -> None:
    async with engine.begin() as conn:
        if "share_replays" not in await existing_columns(conn, "users"):
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN share_replays BOOLEAN DEFAULT 0")
            )
