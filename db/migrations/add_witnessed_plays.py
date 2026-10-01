from db.models.witnessed_play import WitnessedPlay

async def run_witnessed_plays_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: WitnessedPlay.__table__.create(sync, checkfirst=True))
