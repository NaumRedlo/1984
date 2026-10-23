from sqlalchemy import text

from db.migrations._utils import add_column, table_exists
from db.models.local_score import LocalScore

async def run_local_score_checks_migration(engine) -> None:
    async with engine.begin() as conn:
        if not await table_exists(conn, LocalScore.__tablename__):
            return
        for name in ("ranked_date", "checked", "checked_at", "verified"):
            await add_column(conn, LocalScore.__table__.c[name])
        await conn.execute(text('CREATE INDEX IF NOT EXISTS ix_local_scores_checked ON local_scores (checked, beatmap_md5)'))
