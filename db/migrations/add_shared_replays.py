from db.models.shared_replay import SharedReplay

async def run_shared_replays_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: SharedReplay.__table__.create(sync, checkfirst=True))
