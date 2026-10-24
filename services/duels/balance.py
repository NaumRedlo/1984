from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select

from db.models.duel import (Duel, DuelAttempt, DuelAudit, DuelBalanceReview, DuelFeedback, DuelModelVersion, DuelParticipant, DuelPool,
                            DuelPoolEntry, DuelRole, DuelRound)
from db.models.player import Player
from services.duels import catalog, states
from services.duels.errors import DuelError
from utils.timeutils import utcnow

FITS = ("fits", "off")
DIFFICULTIES = ("easy", "fine", "hard")
FAIRNESS = ("fair", "unfair")
PLAYED_WEIGHT = 1.0
PREVIEW_WEIGHT = 0.25
ONE_VOICE_DAYS = 30
REVIEW_NEGATIVES = 3
REVIEW_SHARE = 0.60
EXCLUDE_PLAYED = 5
EXCLUDE_SHARE = 0.75
FEEDBACK_COST = 0.5

OWNER = "OWNER"
BALANCE_REVIEWER = "BALANCE_REVIEWER"
TECHNICAL_OPERATOR = "TECHNICAL_OPERATOR"
MODEL_PUBLISHER = "MODEL_PUBLISHER"
ROLES = (BALANCE_REVIEWER, TECHNICAL_OPERATOR, MODEL_PUBLISHER, OWNER)

class Forbidden(Exception):
    pass

async def roles_of(session, player_id: int, owners: tuple[int, ...] = ()) -> set[str]:
    found = set((await session.scalars(select(DuelRole.role).where(DuelRole.player_id == player_id))).all())
    player = await session.get(Player, player_id)
    if player is not None and player.telegram_id is not None and int(player.telegram_id) in owners:
        found.add(OWNER)
    return found

async def _allowed(session, player_id: int, role: str, owners: tuple[int, ...]) -> str:
    held = await roles_of(session, player_id, owners)
    if role in held:
        return role
    if OWNER in held:
        return OWNER
    raise Forbidden(role)

def _audit(session, actor: int, role: str, action: str, target: str, reason: str, before=None, after=None, now=None) -> None:
    session.add(DuelAudit(actor_id=actor, role=role, action=action, target=target, reason=reason, before=before, after=after,
                          created_at=now or utcnow()))

async def feedback(factory, duel_id: str, player_id: int, slot: str, fit: Optional[str], difficulty: Optional[str],
                   fairness: Optional[str], technical: bool, comment: Optional[str], now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utcnow()
    if fit not in FITS + (None,) or difficulty not in DIFFICULTIES + (None,) or fairness not in FAIRNESS + (None,):
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            duel = await session.get(Duel, duel_id)
            if duel is None:
                raise DuelError("NOT_FOUND")
            mine = await session.scalar(select(DuelParticipant.id).where(DuelParticipant.duel_id == duel_id, DuelParticipant.player_id == player_id))
            if mine is None:
                raise DuelError("NOT_PARTICIPANT")
            entry = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                         .where(DuelPool.duel_id == duel_id, DuelPoolEntry.slot == slot))
            if entry is None or duel.state in (states.INVITED, states.MATCH_OFFERED, states.ACCEPTED, states.GENERATING_POOL,
                                               states.WAITING_ROOM, states.ROOM_SETUP, states.WAITING_PLAYERS):
                raise DuelError("SLOT_UNAVAILABLE")
            played = await session.scalar(select(DuelAttempt.id).join(DuelRound, DuelRound.id == DuelAttempt.round_id).where(
                DuelRound.duel_id == duel_id, DuelRound.slot == slot, DuelAttempt.validity.in_(["valid", "tie"]))) is not None
            held = await session.scalar(select(DuelFeedback).where(DuelFeedback.duel_id == duel_id, DuelFeedback.player_id == player_id,
                                                                  DuelFeedback.slot == slot).with_for_update())
            said = {"fit": fit, "difficulty": difficulty, "fairness": fairness, "technical": technical,
                    "comment": (comment or "")[:500] or None}
            if held is None:
                held = DuelFeedback(duel_id=duel_id, player_id=player_id, slot=slot, beatmap_id=entry.beatmap_id, played=played,
                                    history=[], created_at=now, version=1)
                session.add(held)
            else:
                held.history = list(held.history or []) + [{"fit": held.fit_label, "difficulty": held.difficulty_label,
                                                            "fairness": held.fairness_label, "at": held.updated_at.isoformat()}]
                held.version += 1
            held.played = played
            held.fit_label, held.difficulty_label, held.fairness_label = fit, difficulty, fairness
            held.technical, held.comment, held.updated_at = technical, said["comment"], now
            await session.flush()
            verdict = await weigh(session, int(entry.beatmap_id), now)
            await _act_on(session, int(entry.beatmap_id), entry.revision_hash, verdict, now)
            return {"slot": slot, "played": played, "version": held.version, "map": verdict}

async def weigh(session, beatmap_id: int, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utcnow()
    rows = (await session.scalars(select(DuelFeedback).where(DuelFeedback.beatmap_id == beatmap_id, DuelFeedback.fit_label.is_not(None),
                                                            DuelFeedback.updated_at >= now - timedelta(days=ONE_VOICE_DAYS))
                                  .order_by(DuelFeedback.updated_at))).all()
    latest: dict[int, DuelFeedback] = {}
    for row in rows:
        latest[row.player_id] = row
    bad = total = 0.0
    negatives = played = 0
    for row in latest.values():
        weight = PLAYED_WEIGHT if row.played else PREVIEW_WEIGHT
        total += weight
        if row.fit_label == "off":
            bad += weight
            negatives += 1
        played += row.played
    share = (2 + bad) / (4 + total)
    return {"q_bad": share, "negatives": negatives, "played": played, "voices": len(latest)}

def cost_of(share: float) -> float:
    return FEEDBACK_COST * max(0.0, share - 0.5)

async def _open_review(session, beatmap_id: int, revision: Optional[str], reason: str, now: datetime) -> bool:
    held = await session.scalar(select(DuelBalanceReview.id).where(DuelBalanceReview.beatmap_id == beatmap_id,
                                                                   DuelBalanceReview.decision.is_(None)))
    if held is not None:
        return False
    session.add(DuelBalanceReview(beatmap_id=beatmap_id, revision_hash=revision, reason=reason, requested_at=now))
    return True

async def _act_on(session, beatmap_id: int, revision: Optional[str], verdict: dict[str, Any], now: datetime) -> None:
    if verdict["played"] >= EXCLUDE_PLAYED and verdict["q_bad"] >= EXCLUDE_SHARE:
        await catalog.quarantine(session, beatmap_id, "feedback_exclusion", now)
        await _open_review(session, beatmap_id, revision, "feedback_exclusion", now)
    elif verdict["negatives"] >= REVIEW_NEGATIVES and verdict["q_bad"] >= REVIEW_SHARE:
        await _open_review(session, beatmap_id, revision, "feedback", now)

async def uncertain_revealed(session, duel_id: str, now: datetime) -> int:
    entries = (await session.scalars(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                     .where(DuelPool.duel_id == duel_id, DuelPoolEntry.review_state == "uncertain"))).all()
    made = 0
    for entry in entries:
        made += await _open_review(session, int(entry.beatmap_id), entry.revision_hash, "uncertain_revealed", now)
    return made

async def review_map(factory, actor: int, beatmap_id: int, decision: str, reason: str, owners: tuple[int, ...] = (),
                     slot: Optional[str] = None, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utcnow()
    if not reason.strip():
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            after = await catalog.review(session, beatmap_id, decision, actor, reason, slot=slot, now=now)
            for review in (await session.scalars(select(DuelBalanceReview).where(DuelBalanceReview.beatmap_id == beatmap_id,
                                                                                 DuelBalanceReview.decision.is_(None)))).all():
                review.decision, review.reviewer_id, review.decided_at = decision, actor, now
            _audit(session, actor, role, f"map_{decision.lower()}", str(beatmap_id), reason, after=after, now=now)
            return after

async def note_discovery(factory, actor: int, least: float, most: float, reason: str, owners: tuple[int, ...] = (),
                         now: Optional[datetime] = None) -> None:
    if not reason.strip():
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            _audit(session, actor, role, "discover", f"{least:.1f}-{most:.1f}", reason.strip(), now=now)

async def note_training(factory, actor: int, reason: str, owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> None:
    if not reason.strip():
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            _audit(session, actor, role, "train_model", "kamerton", reason.strip(), now=now)

async def open_reviews(factory, actor: int, owners: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    async with factory() as session:
        await _allowed(session, actor, BALANCE_REVIEWER, owners)
        rows = (await session.scalars(select(DuelBalanceReview).where(DuelBalanceReview.decision.is_(None))
                                      .order_by(DuelBalanceReview.requested_at))).all()
        found = []
        for row in rows:
            found.append({"id": row.id, "beatmap_id": row.beatmap_id, "reason": row.reason, "since": row.requested_at.isoformat() + "Z",
                          "feedback": await weigh(session, int(row.beatmap_id))})
        return found

async def grant(factory, actor: int, player_id: int, role: str, reason: str, owners: tuple[int, ...] = (),
                now: Optional[datetime] = None) -> None:
    if role not in ROLES or role == OWNER:
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            granted = await _allowed(session, actor, OWNER, owners)
            if await session.get(DuelRole, (player_id, role)) is None:
                session.add(DuelRole(player_id=player_id, role=role, granted_by=actor, granted_at=now or utcnow()))
            _audit(session, actor, granted, "grant", f"{player_id}:{role}", reason, now=now)

async def operator_cancel(factory, actor: int, duel_id: str, reason: str, owners: tuple[int, ...] = ()) -> Optional[dict[str, Any]]:
    from services.duels import service

    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, TECHNICAL_OPERATOR, owners)
            _audit(session, actor, role, "cancel", duel_id, reason)
    return await service.cancel(factory, duel_id, "operator_cancel")

async def manual_finalize(factory, actor: int, duel_id: str, match_id: int, game_id: int, reason: str, osu, budget,
                          owners: tuple[int, ...] = ()) -> Optional[dict[str, Any]]:
    from services.duels import series, verifier
    from services.duels.generator import SLOT_MODS

    async with factory() as session:
        role = await _allowed(session, actor, TECHNICAL_OPERATOR, owners)
        duel = await session.get(Duel, duel_id)
        if duel is None or duel.state != states.VERIFYING:
            raise DuelError("INVALID_STATE")
        current = await session.scalar(select(DuelRound).where(DuelRound.duel_id == duel_id).order_by(DuelRound.ordinal.desc()))
        attempt = await session.scalar(select(DuelAttempt).where(DuelAttempt.round_id == current.id).order_by(DuelAttempt.attempt_no.desc()))
        entry = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                     .where(DuelPool.duel_id == duel_id, DuelPoolEntry.slot == current.slot))
        people = (await session.scalars(select(DuelParticipant).where(DuelParticipant.duel_id == duel_id))).all()
    events, _ = await verifier.history(osu, match_id, None, budget)
    game = verifier.games(events).get(game_id)
    if game is None:
        raise DuelError("EVIDENCE_PENDING")
    started = attempt.commanded_at or attempt.start_at
    expected = verifier.Expected(int(entry.beatmap_id), frozenset(int(p.osu_user_id) for p in people), frozenset(SLOT_MODS[current.slot]),
                                 current.slot == "TB", (started - timedelta(seconds=10)) if started else datetime.min)
    verdict = verifier.judge(game, expected)
    if verdict.state in ("pending",):
        raise DuelError("EVIDENCE_PENDING")
    async with factory() as session:
        async with session.begin():
            row = await session.get(DuelAttempt, attempt.id)
            row.match_id = match_id
            if not await verifier.bind(session, attempt.id, game_id):
                raise DuelError("INVALID_STATE")
            _audit(session, actor, role, "manual_finalize", duel_id, reason, after={"match": match_id, "game": game_id, "state": verdict.state})
    return await series.round_verdict(factory, duel_id, {
        "state": verdict.state, "winner": verdict.winner, "scores": {str(k): v for k, v in verdict.scores.items()},
        "mods": {str(k): v for k, v in verdict.mods.items()}, "reasons": verdict.reasons, "game_id": game_id,
        "details": {str(k): v for k, v in verdict.details.items()}})

async def train(factory, version: str, model_body: dict[str, Any], report: dict[str, Any], labels: int,
                now: Optional[datetime] = None) -> None:
    async with factory() as session:
        async with session.begin():
            session.add(DuelModelVersion(version=version, state="shadow", body=model_body, report=report, labels=labels,
                                         created_at=now or utcnow()))

async def publish(factory, actor: int, version: str, reason: str, owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> None:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, MODEL_PUBLISHER, owners)
            chosen = await session.get(DuelModelVersion, version)
            if chosen is None or chosen.state not in ("shadow", "retired"):
                raise DuelError("INVALID_STATE")
            for row in (await session.scalars(select(DuelModelVersion).where(DuelModelVersion.state == "published"))).all():
                row.state = "retired"
            chosen.state, chosen.published_at, chosen.published_by = "published", now, actor
            _audit(session, actor, role, "publish_model", version, reason, now=now)

async def delete_model(factory, actor: int, version: str, reason: str, owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> None:
    if not reason.strip():
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, MODEL_PUBLISHER, owners)
            chosen = await session.get(DuelModelVersion, version)
            if chosen is None or chosen.state == "published":
                raise DuelError("INVALID_STATE")
            await session.delete(chosen)
            _audit(session, actor, role, "delete_model", version, reason.strip(), before={"state": chosen.state}, now=now)

async def rollback(factory, actor: int, reason: str, owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> Optional[str]:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, MODEL_PUBLISHER, owners)
            current = await session.scalar(select(DuelModelVersion).where(DuelModelVersion.state == "published"))
            earlier = await session.scalar(select(DuelModelVersion).where(DuelModelVersion.state == "retired",
                                                                          DuelModelVersion.published_at.is_not(None))
                                           .order_by(DuelModelVersion.published_at.desc()))
            if current is None or earlier is None:
                raise DuelError("INVALID_STATE")
            current.state = "retired"
            earlier.state = "published"
            _audit(session, actor, role, "rollback_model", earlier.version, reason, before={"published": current.version}, now=now)
            return earlier.version

async def published(factory) -> Optional[DuelModelVersion]:
    async with factory() as session:
        return await session.scalar(select(DuelModelVersion).where(DuelModelVersion.state == "published"))

async def corpus_table(factory, actor: int, owners: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    from db.models.duel_catalog import DuelMapRevision, DuelTrainingExample
    from services.duels import corpus, importer

    async with factory() as session:
        await _allowed(session, actor, BALANCE_REVIEWER, owners)
        labels = await importer.corpus_labels(session)
        rows = (await session.scalars(select(DuelTrainingExample).where(DuelTrainingExample.corpus_version.in_(corpus.LEARNED))
                                      .order_by(DuelTrainingExample.corpus_version, DuelTrainingExample.year.desc(),
                                                DuelTrainingExample.stage, DuelTrainingExample.original_slot))).all()
        hashes = [row.revision_hash for row in rows if row.revision_hash]
        revisions = {revision.hash: revision for revision in (await session.scalars(select(DuelMapRevision).where(DuelMapRevision.hash.in_(hashes or [""])))).all()}
    cells: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (row.corpus_version, row.year, row.stage, row.original_slot)
        cell = cells.setdefault(key, {"corpus": row.corpus_version, "year": row.year, "stage": row.stage, "slot": row.original_slot,
                                      "label": labels.get(key + (None,)), "maps": []})
        label, verified = importer.resolved(row, labels)
        cell["maps"].append({"beatmap_id": int(row.beatmap_id), "beatmapset_id": int(row.beatmapset_id) if row.beatmapset_id else None,
                             "label": label or corpus.EXCLUDED, "verified": verified, "file": row.revision_hash is not None,
                             "title": getattr(revisions.get(row.revision_hash), "title", None), "artist": getattr(revisions.get(row.revision_hash), "artist", None),
                             "version": getattr(revisions.get(row.revision_hash), "version", None), "mapper": getattr(revisions.get(row.revision_hash), "mapper", None),
                             "conflict": bool(row.conflict)})
    return list(cells.values())

async def label_corpus(factory, actor: int, corpus_version: str, year: int, stage: str, original_slot: str, beatmap_id: Optional[int],
                       label: str, reason: str, owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> dict[str, Any]:
    from db.models.duel_catalog import DuelCorpusLabel
    from services.duels import corpus

    now = now or utcnow()
    if not corpus.allowed(label) or corpus_version not in corpus.LEARNED or not reason.strip():
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            row = await session.scalar(select(DuelCorpusLabel).where(
                DuelCorpusLabel.corpus_version == corpus_version, DuelCorpusLabel.year == year, DuelCorpusLabel.stage == stage,
                DuelCorpusLabel.original_slot == original_slot,
                DuelCorpusLabel.beatmap_id.is_(None) if beatmap_id is None else DuelCorpusLabel.beatmap_id == beatmap_id))
            before = {"label": row.label, "reviewer": row.reviewer_id, "reason": row.reason} if row is not None else None
            if row is None:
                row = DuelCorpusLabel(corpus_version=corpus_version, year=year, stage=stage, original_slot=original_slot, beatmap_id=beatmap_id)
                session.add(row)
            row.label, row.reviewer_id, row.reason, row.updated_at = label, actor, reason.strip(), now
            target = f"{year}:{stage}:{original_slot}" + (f":{beatmap_id}" if beatmap_id is not None else "")
            after = {"label": label}
            _audit(session, actor, role, "corpus_label", target[:64], reason.strip(), before=before, after=after, now=now)
            return after

async def add_tournament(factory, actor: int, name: str, year: int, text: str, reason: str, owners: tuple[int, ...] = (),
                         now: Optional[datetime] = None) -> dict[str, Any]:
    from db.models.duel_catalog import DuelCorpusLabel, DuelTrainingExample
    from services.duels import corpus

    now = now or utcnow()
    name = name.strip()[:120]
    if not name or not reason.strip():
        raise DuelError("INVALID_STATE")
    found = corpus.read_pools(text, year, f"own:{name}")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            added = 0
            for example in corpus.distinct(found):
                label = corpus.label(example.original_slot)
                if label is None:
                    continue
                stage = name[:32]
                held = await session.scalar(select(DuelTrainingExample).where(
                    DuelTrainingExample.corpus_version == corpus.OWN_VERSION, DuelTrainingExample.year == year,
                    DuelTrainingExample.stage == stage, DuelTrainingExample.original_slot == example.original_slot,
                    DuelTrainingExample.beatmap_id == example.beatmap_id))
                if held is not None:
                    continue
                session.add(DuelTrainingExample(corpus_version=corpus.OWN_VERSION, source=f"own:{name}"[:256], year=year, stage=stage,
                                                original_slot=example.original_slot, beatmap_id=example.beatmap_id,
                                                beatmapset_id=example.beatmapset_id, normalized_label=label, conflict=False, fetched_at=now))
                session.add(DuelCorpusLabel(corpus_version=corpus.OWN_VERSION, year=year, stage=stage, original_slot=example.original_slot,
                                            beatmap_id=example.beatmap_id, label=label, reviewer_id=actor, reason=reason.strip(), updated_at=now))
                added += 1
            _audit(session, actor, role, "add_tournament", name[:64], reason.strip(), after={"maps": added, "year": year}, now=now)
    return {"added": added, "read": len(found)}
