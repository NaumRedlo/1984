from db.models.left_member import LeftMember

async def run_left_members_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: LeftMember.__table__.create(sync, checkfirst=True))
