from db.migrations._utils import add_column, table_exists
from db.models.duel import Duel, DuelAttempt, DuelDeadline, DuelParticipant
from db.models.duel_catalog import DuelMapRevision

async def run_duel_phases_migration(engine) -> None:
    async with engine.begin() as conn:
        for model, names in ((Duel, ("phase",)), (DuelDeadline, ("phase",)), (DuelParticipant, ("missed", "disconnects")), (DuelAttempt, ("commanded_at",)), (DuelMapRevision, ("max_combo",))):
            if not await table_exists(conn, model.__tablename__):
                continue
            for name in names:
                await add_column(conn, model.__table__.c[name])
