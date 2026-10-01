from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists
from db.models.shared_video import SharedVideo, VideoDelivery

async def run_video_sharing_migration(engine) -> None:
    async with engine.begin() as conn:
        if await table_exists(conn, "players") and "accept_videos" not in await existing_columns(conn, "players"):
            await conn.execute(text("ALTER TABLE players ADD COLUMN accept_videos VARCHAR(16)"))
        await conn.run_sync(lambda sync: SharedVideo.__table__.create(sync, checkfirst=True))
        await conn.run_sync(lambda sync: VideoDelivery.__table__.create(sync, checkfirst=True))
