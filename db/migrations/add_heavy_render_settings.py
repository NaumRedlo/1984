from sqlalchemy import text

from db.migrations._utils import existing_columns

async def run_heavy_render_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        for name, kind in (
            ("render_background", "BOOLEAN"),
            ("render_bare", "BOOLEAN"),
            ("heavy_renders", "INTEGER"),
            ("heavy_renders_on", "VARCHAR(10)"),
        ):
            if name not in columns:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {kind}"))
