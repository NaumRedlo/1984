import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from db.models.duel import DuelModelVersion
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from services.duels import balance
from services.duels.features import FEATURE_VERSION
from services.duels.generator import SLOT_MODS
from services.duels.slot_model import CLASSES, OTHER, SlotModel, _softmax, report, vector
from utils.timeutils import utcnow

NEW_LABELS_LEAST = 50
EVERY = timedelta(days=1)
HELD_OUT = 5

def held_out(set_id: int) -> bool:
    return int(hashlib.sha256(str(set_id).encode()).hexdigest(), 16) % HELD_OUT == 0

async def labelled(session) -> list[tuple[list[float], str, int]]:
    rows = (await session.execute(
        select(DuelMapAssignment, DuelMapRevision)
        .join(DuelMapRevision, (DuelMapRevision.beatmap_id == DuelMapAssignment.beatmap_id)
              & (DuelMapRevision.hash == DuelMapAssignment.revision_hash))
        .where(DuelMapAssignment.state.in_(["HUMAN_APPROVED", "REJECTED"]))
    )).all()
    found = []
    for assignment, revision in rows:
        label = assignment.slot if assignment.state == "HUMAN_APPROVED" else OTHER
        mods = "".join(SLOT_MODS[assignment.slot]) if assignment.slot else "NF"
        analysis = await session.scalar(select(DuelMapAnalysis).where(DuelMapAnalysis.revision_hash == revision.hash,
                                                                     DuelMapAnalysis.mods == mods,
                                                                     DuelMapAnalysis.feature_version == FEATURE_VERSION))
        if analysis is None or not analysis.features:
            continue
        found.append((vector(analysis.features), label, int(revision.beatmapset_id or assignment.beatmap_id)))
    return found

async def maybe_train(factory, now: Optional[datetime] = None, steps: int = 400) -> Optional[str]:
    now = now or utcnow()
    async with factory() as session:
        last = await session.scalar(select(DuelModelVersion).order_by(DuelModelVersion.created_at.desc()))
        if last is not None and now - last.created_at < EVERY:
            return None
        data = await labelled(session)
    before = last.labels if last is not None else 0
    if len(data) - before < NEW_LABELS_LEAST:
        return None
    train = [item for item in data if not held_out(item[2])]
    test = [item for item in data if held_out(item[2])]
    if not train or len({label for _, label, _ in train}) < 2:
        return None
    model = await asyncio.to_thread(SlotModel.fit, [row for row, _, _ in train], [label for _, label, _ in train], 0.01, steps)
    guessed = []
    for row, _, _ in test:
        normalised = model.normaliser.apply(row)
        probabilities = _softmax([b + sum(w * x for w, x in zip(ws, normalised)) for ws, b in zip(model.weights, model.bias)])
        best = max(range(len(probabilities)), key=probabilities.__getitem__)
        guessed.append(None if CLASSES[best] == OTHER else CLASSES[best])
    found = report([label for _, label, _ in test], guessed)
    version = f"slots-{now.strftime('%Y%m%d%H%M')}-{len(data)}"
    await balance.train(factory, version, model.dump(), {**found, "train": len(train), "test": len(test)}, len(data), now)
    return version
