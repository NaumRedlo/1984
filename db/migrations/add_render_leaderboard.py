from sqlalchemy import text

from db.migrations._utils import existing_columns

async def run_render_leaderboard_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_leaderboard" not in columns:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN render_leaderboard BOOLEAN")
            )
