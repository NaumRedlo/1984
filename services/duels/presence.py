from datetime import timedelta
from hashlib import sha256

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from db.models.duel_catalog import DuelDeskPresence
from db.models.player import Player
from services.duels import balance
from utils.timeutils import utcnow

TTL = timedelta(seconds=45)
SECTIONS = {"queue", "label", "owc", "catalog", "model", "duels", "journal", "people", "live"}


async def touch(factory, actor, section, beatmap=None, owners=(), now=None):
    if section is not None and (not isinstance(section, str) or section not in SECTIONS):
        raise ValueError("section")
    if beatmap is not None and (type(beatmap) is not int or beatmap <= 0):
        raise ValueError("beatmap")
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            roles = await balance.roles_of(session, actor, owners)
            if not roles:
                raise balance.Forbidden(balance.BALANCE_REVIEWER)
            await session.execute(delete(DuelDeskPresence).where(DuelDeskPresence.updated_at <= now - TTL))
            if section is None:
                await session.execute(delete(DuelDeskPresence).where(DuelDeskPresence.player_id == actor))
            else:
                insert = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
                values = dict(player_id=actor, section=section, beatmap_id=beatmap, updated_at=now)
                await session.execute(insert(DuelDeskPresence).values(**values).on_conflict_do_update(
                    index_elements=[DuelDeskPresence.player_id], set_=values))
            rows = (await session.execute(select(DuelDeskPresence, Player).join(Player, Player.id == DuelDeskPresence.player_id))).all()
            result = []
            for active, player in rows:
                if not await balance.roles_of(session, player.id, owners):
                    continue
                result.append({"player": player.id, "name": player.osu_username or str(player.id),
                               "section": active.section, "beatmap": active.beatmap_id, "mine": player.id == actor,
                               "updated_at": active.updated_at.isoformat() + "Z"})
            return sorted(result, key=lambda row: (not row["mine"], row["name"].lower()))


async def revision(factory):
    from db.models.duel import Duel, DuelAudit, DuelFeedback, DuelModelVersion
    from db.models.duel_catalog import DuelCorpusLabel, DuelMapAnalysis, DuelMapAssignment, DuelTrainingExample

    # Presence heartbeats do not change catalogue data or invalidate other readers.
    columns = []
    for model, stamp in ((Duel, Duel.updated_at), (DuelAudit, DuelAudit.created_at),
                         (DuelFeedback, DuelFeedback.updated_at), (DuelModelVersion, DuelModelVersion.created_at),
                         (DuelMapAssignment, DuelMapAssignment.updated_at), (DuelMapAnalysis, DuelMapAnalysis.created_at),
                         (DuelCorpusLabel, DuelCorpusLabel.updated_at), (DuelTrainingExample, DuelTrainingExample.fetched_at)):
        columns.extend((select(func.count()).select_from(model).scalar_subquery(),
                        select(func.max(stamp)).scalar_subquery()))
    async with factory() as session:
        values = (await session.execute(select(*columns))).one()
    return sha256("|".join(str(value) for value in values).encode()).hexdigest()
