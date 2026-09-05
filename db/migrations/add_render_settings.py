from sqlalchemy import text

from db.migrations._utils import existing_columns

async def run_render_settings_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        for name, kind in (
            ("render_size", "VARCHAR(16)"),
            ("render_fps", "INTEGER"),
            ("render_mute", "BOOLEAN"),
            ("render_skin", "VARCHAR(64)"),
        ):
            if name not in columns:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {kind}"))
