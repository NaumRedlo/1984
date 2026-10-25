import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select

from db.models.duel import DuelAudit, DuelModelVersion
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from services.duels import balance
from services.duels.features import FEATURE_VERSION
from services.duels.generator import SLOT_MODS
from services.duels.importer import SEEN_AS
from services.duels.slot_model import OTHER, SlotModel, vector
from utils.timeutils import utcnow

NAME = "kamerton"
NEW_LABELS_LEAST = 50
EVERY = timedelta(days=1)
HELD_OUT = 5
busy = asyncio.Lock()

def held_out(set_id: int) -> bool:
    return int(hashlib.sha256(str(set_id).encode()).hexdigest(), 16) % HELD_OUT == 0
async def labelled(session) -> list[tuple[list[float], str, int]]:
    rows = (await session.execute(
        select(DuelMapAssignment, DuelMapRevision)
        .join(DuelMapRevision, (DuelMapRevision.beatmap_id == DuelMapAssignment.beatmap_id)
              & (DuelMapRevision.hash == DuelMapAssignment.revision_hash))
        .where(DuelMapAssignment.state == "HUMAN_APPROVED", DuelMapAssignment.slot.is_not(None))
    )).all()
    found = []
    for assignment, revision in rows:
        label = assignment.slot
        analysis = await session.scalar(select(DuelMapAnalysis).where(DuelMapAnalysis.revision_hash == revision.hash,
                                                                     DuelMapAnalysis.mods == SEEN_AS,
                                                                     DuelMapAnalysis.feature_version == FEATURE_VERSION))
        if analysis is None or not analysis.features:
            continue
        found.append((vector(analysis.features), label, int(revision.beatmapset_id or assignment.beatmap_id)))
    return found

async def _gathered(factory) -> tuple[list, list]:
    from services.duels import importer
    from services.duels.rules import reserved

    found = await importer.corpus_rows(factory)
    async with factory() as session:
        decided = await labelled(session)
    known = lambda label: label in SLOT_MODS or reserved(label)
    return [row for row in found if known(row[1])], [row for row in decided if known(row[1])]

def fingerprint(rows) -> str:
    made = hashlib.sha256()
    for features, label, set_id in sorted(rows, key=lambda row: (row[2], row[1], [round(value, 6) for value in row[0]])):
        made.update(f"{set_id}:{label}:{','.join(f'{value:.6f}' for value in features)};".encode())
    return made.hexdigest()

async def unchanged(factory) -> bool:
    found, decided = await _gathered(factory)
    rows = [(row[0], row[1], row[2]) for row in found] + decided
    async with factory() as session:
        last = await session.scalar(select(DuelModelVersion).where(DuelModelVersion.version.like(f"{NAME}-%"))
                                    .order_by(DuelModelVersion.created_at.desc()))
    return last is not None and (last.report or {}).get("fingerprint") == fingerprint(rows)

def _people(found, decided) -> int:
    return sum(1 for row in found if row[5]) + len(decided)

async def _number(session) -> int:
    names = list((await session.scalars(select(DuelModelVersion.version).where(DuelModelVersion.version.like(f"{NAME}-%")))).all())
    names += (await session.scalars(select(DuelAudit.target).where(DuelAudit.action == "delete_model", DuelAudit.target.like(f"{NAME}-%")))).all()
    return max((int(name.split("-", 1)[1]) for name in names if name.split("-", 1)[1].isdigit()), default=0) + 1

async def train(factory, steps: int = 400, now: Optional[datetime] = None) -> Optional[tuple[str, dict[str, Any]]]:
    async with busy:
        return await _train(factory, steps, now)

async def _train(factory, steps: int, now: Optional[datetime]) -> Optional[tuple[str, dict[str, Any]]]:
    from services.duels import importer

    now = now or utcnow()
    found, decided = await _gathered(factory)
    rows = [(row[0], row[1], row[2]) for row in found] + decided
    if len({label for _, label, _ in rows}) < 2:
        return None
    printed = fingerprint(rows)
    async with factory() as session:
        last = await session.scalar(select(DuelModelVersion).where(DuelModelVersion.version.like(f"{NAME}-%"))
                                    .order_by(DuelModelVersion.created_at.desc()))
    if last is not None and (last.report or {}).get("fingerprint") == printed:
        return None
    truth, guessed, sure, years = [], [], [], {}
    folds = [(str(year), [(row[0], row[1], row[2]) for row in found if not row[4] and row[3] == year])
             for year in sorted({row[3] for row in found if not row[4]})]
    folds.append(("queue", [row for row in decided if held_out(row[2])]))
    for name, held in folds:
        if not held:
            continue
        sets = {set_id for _, _, set_id in held}
        rest = [row for row in rows if row[2] not in sets]
        if len({label for _, label, _ in rest}) < 2:
            continue
        model = await asyncio.to_thread(SlotModel.fit, [row for row, _, _ in rest], [label for _, label, _ in rest], 0.01, steps)
        part = importer.guesses(model, held)
        years[name] = importer.judged(*part)
        truth, guessed, sure = truth + part[0], guessed + part[1], sure + part[2]
    model = await asyncio.to_thread(SlotModel.fit, [row for row, _, _ in rows], [label for _, label, _ in rows], 0.01, steps)
    async with factory() as session:
        number = await _number(session)
    version = f"{NAME}-{number}"
    model.version = version
    found_report = {"name": NAME, "number": number, "maps": len(rows), "corpus": len(found), "own": sum(1 for row in found if row[4]),
                    "queue": len(decided), "verified": sum(1 for row in found if row[5]), "test": importer.judged(truth, guessed, sure),
                    "years": years, "fingerprint": printed}
    await balance.train(factory, version, model.dump(), found_report, _people(found, decided), now)
    return version, found_report

async def maybe_train(factory, now: Optional[datetime] = None, steps: int = 400) -> Optional[str]:
    if busy.locked():
        return None
    now = now or utcnow()
    async with factory() as session:
        last = await session.scalar(select(DuelModelVersion).order_by(DuelModelVersion.created_at.desc()))
    if last is not None and now - last.created_at < EVERY:
        return None
    found, decided = await _gathered(factory)
    before = last.labels if last is not None and last.version.startswith(f"{NAME}-") else 0
    if _people(found, decided) - before < NEW_LABELS_LEAST:
        return None
    made = await train(factory, steps, now)
    return made[0] if made is not None else None
