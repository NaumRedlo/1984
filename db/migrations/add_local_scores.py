from db.models.local_score import LocalScore

async def run_local_scores_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: LocalScore.__table__.create(sync, checkfirst=True))
