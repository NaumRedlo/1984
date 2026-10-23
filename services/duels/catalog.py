from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from db.models.duel_catalog import DuelAssignmentLog, DuelMapAssignment, DuelMapRevision
from services.duels.rules import SLOTS
from utils.timeutils import utcnow

UNASSIGNED = "UNASSIGNED"
AUTO_ASSIGNED = "AUTO_ASSIGNED"
HUMAN_APPROVED = "HUMAN_APPROVED"
REJECTED = "REJECTED"
QUARANTINED = "QUARANTINED"

CONFIDENT = (0.80, 0.20)
UNCERTAIN = (0.60, 0.10)

class CatalogRefusal(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

def confidence_level(probability: Optional[float], margin: Optional[float]) -> Optional[str]:
    if probability is None or margin is None:
        return None
    if probability >= CONFIDENT[0] and margin >= CONFIDENT[1]:
        return "confident"
    if probability >= UNCERTAIN[0] and margin >= UNCERTAIN[1]:
        return "uncertain"
    return None

def _view(row: Optional[DuelMapAssignment]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {"slot": row.slot, "state": row.state, "confidence": row.confidence, "margin": row.margin,
            "version": row.assignment_version, "revision": row.revision_hash, "admitted": row.admitted,
            "locked": row.locked_at is not None}

async def _log(session, beatmap_id: int, actor_id: Optional[int], action: str, before, after, reason: Optional[str], now: datetime):
    session.add(DuelAssignmentLog(beatmap_id=beatmap_id, actor_id=actor_id, action=action, before=before, after=after,
                                  reason=reason, created_at=now))

async def _row(session, beatmap_id: int) -> Optional[DuelMapAssignment]:
    return await session.scalar(select(DuelMapAssignment).where(DuelMapAssignment.beatmap_id == beatmap_id).with_for_update())

async def propose(session, beatmap_id: int, slot: str, probability: float, margin: float, version: str,
                  now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utcnow()
    if slot not in SLOTS:
        raise CatalogRefusal("UNKNOWN_SLOT")
    row = await _row(session, beatmap_id)
    before = _view(row)
    if row is not None and row.state in (HUMAN_APPROVED, REJECTED, QUARANTINED):
        raise CatalogRefusal("DECIDED_BY_HUMAN" if row.state != QUARANTINED else "QUARANTINED")
    if row is not None and row.slot is not None and row.slot != slot:
        raise CatalogRefusal("SLOT_FIXED")
    level = confidence_level(probability, margin)
    if row is None:
        row = DuelMapAssignment(beatmap_id=beatmap_id)
        session.add(row)
    if row.slot is None:
        row.slot = slot if level else None
    row.state = AUTO_ASSIGNED if row.slot is not None and level else UNASSIGNED
    row.confidence = probability
    row.margin = margin
    row.assignment_version = version
    row.updated_at = now
    await session.flush()
    await _log(session, beatmap_id, None, "propose", before, _view(row), level or "below_threshold", now)
    return _view(row)

async def review(session, beatmap_id: int, decision: str, reviewer_id: int, note: str, slot: Optional[str] = None,
                 now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utcnow()
    row = await _row(session, beatmap_id)
    if row is None:
        raise CatalogRefusal("NOT_FOUND")
    before = _view(row)
    if decision == "APPROVE":
        if row.state == REJECTED:
            raise CatalogRefusal("REJECTED_STAYS")
        wanted = slot or row.slot
        if wanted not in SLOTS:
            raise CatalogRefusal("UNKNOWN_SLOT")
        if row.slot is not None and wanted != row.slot:
            raise CatalogRefusal("SLOT_FIXED")
        row.slot = wanted
        row.state = HUMAN_APPROVED
    elif decision == "REJECT":
        row.state = REJECTED
    elif decision == "QUARANTINE":
        row.state = QUARANTINED
    else:
        raise CatalogRefusal("UNKNOWN_DECISION")
    row.reviewer_id = reviewer_id
    row.reviewed_at = now
    row.note = note
    row.updated_at = now
    await session.flush()
    await _log(session, beatmap_id, reviewer_id, decision.lower(), before, _view(row), note, now)
    return _view(row)

async def quarantine(session, beatmap_id: int, reason: str, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    now = now or utcnow()
    row = await _row(session, beatmap_id)
    if row is None or row.state in (REJECTED, QUARANTINED):
        return _view(row)
    before = _view(row)
    row.state = QUARANTINED
    row.note = reason
    row.updated_at = now
    await session.flush()
    await _log(session, beatmap_id, None, "quarantine", before, _view(row), reason, now)
    return _view(row)

async def lock(session, beatmap_ids: list[int], now: Optional[datetime] = None) -> None:
    now = now or utcnow()
    rows = (await session.scalars(
        select(DuelMapAssignment).where(DuelMapAssignment.beatmap_id.in_(beatmap_ids)).with_for_update()
    )).all()
    for row in rows:
        if row.locked_at is None:
            row.locked_at = now
            await _log(session, row.beatmap_id, None, "lock", None, _view(row), None, now)
    await session.flush()

async def revision(session, beatmap_id: int, digest: str, *, beatmapset_id: Optional[int] = None, ranked_status: Optional[str] = None,
                   mapper: Optional[str] = None, source: Optional[str] = None, object_count: Optional[int] = None,
                   format_version: Optional[int] = None, now: Optional[datetime] = None) -> tuple[DuelMapRevision, bool]:
    now = now or utcnow()
    held = await session.scalar(select(DuelMapRevision).where(DuelMapRevision.beatmap_id == beatmap_id,
                                                             DuelMapRevision.hash == digest))
    if held is not None:
        return held, False
    made = DuelMapRevision(beatmap_id=beatmap_id, beatmapset_id=beatmapset_id, hash=digest, ranked_status=ranked_status,
                           mapper=mapper, source=source, object_count=object_count, format_version=format_version,
                           status="pending", downloaded_at=now)
    session.add(made)
    row = await _row(session, beatmap_id)
    if row is not None and row.revision_hash != digest:
        before = _view(row)
        row.revision_hash = digest
        row.admitted = False
        row.updated_at = now
        await _log(session, beatmap_id, None, "revision", before, _view(row), "map_revision_changed", now)
    elif row is None:
        session.add(DuelMapAssignment(beatmap_id=beatmap_id, state=UNASSIGNED, revision_hash=digest, admitted=False, updated_at=now))
    await session.flush()
    return made, True

async def admit(session, beatmap_id: int, digest: str, ok: bool, reason: Optional[str] = None,
                now: Optional[datetime] = None) -> None:
    now = now or utcnow()
    made = await session.scalar(select(DuelMapRevision).where(DuelMapRevision.beatmap_id == beatmap_id,
                                                             DuelMapRevision.hash == digest).with_for_update())
    if made is None:
        raise CatalogRefusal("NOT_FOUND")
    made.status = "ok" if ok else "quarantined"
    made.reason = reason
    row = await _row(session, beatmap_id)
    if row is not None and row.revision_hash == digest:
        row.admitted = ok
        row.updated_at = now
    if not ok:
        await quarantine(session, beatmap_id, reason or "parse_failed", now)
    await session.flush()

def playable(row: DuelMapAssignment, revision_row: Optional[DuelMapRevision]) -> Optional[str]:
    if row.slot is None or not row.admitted or revision_row is None or revision_row.status != "ok":
        return None
    if revision_row.ranked_status != "ranked":
        return None
    if row.state == HUMAN_APPROVED:
        return "human"
    if row.state == AUTO_ASSIGNED:
        return confidence_level(row.confidence, row.margin)
    return None
