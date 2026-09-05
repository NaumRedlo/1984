from sqlalchemy import text

from db.migrations._utils import existing_columns

async def run_render_levels_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        for name in ("render_music", "render_hitsounds"):
            if name not in columns:
                await conn.execute(
                    text(f"ALTER TABLE users ADD COLUMN {name} INTEGER")
                )
