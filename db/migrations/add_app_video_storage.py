from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists


async def run_app_video_storage_migration(engine) -> None:
    async with engine.begin() as conn:
        if not await table_exists(conn, "shared_videos"):
            return
        columns = await existing_columns(conn, "shared_videos")
        for name, kind in (("storage_hash", "VARCHAR(64)"), ("stored_until", "DATETIME"),
                           ("skin_name", "VARCHAR(128)"), ("skin_hash", "VARCHAR(64)"),
                           ("owner_player_id", "INTEGER"), ("replay_sha256", "VARCHAR(64)")):
            if name not in columns:
                await conn.execute(text(f"ALTER TABLE shared_videos ADD COLUMN {name} {kind}"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_shared_videos_stored_until ON shared_videos (stored_until)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_shared_videos_owner_player_id ON shared_videos (owner_player_id)"))
