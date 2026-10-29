from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select

from db.models.duel import (Duel, DuelAttempt, DuelAudit, DuelBalanceReview, DuelFeedback, DuelModelVersion, DuelParticipant, DuelPool,
                            DuelPoolEntry, DuelRole, DuelRound)
from db.models.player import Player
from services.duels import catalog, states
from services.duels.errors import DuelError
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger(__name__)

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
                     slot: Optional[str] = None, now: Optional[datetime] = None, secondary: Any = catalog.KEEP) -> dict[str, Any]:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            before = catalog._view(await catalog._row(session, beatmap_id))
            after = await catalog.review(session, beatmap_id, decision, actor, reason, slot=slot, now=now, secondary=secondary)
            for review in (await session.scalars(select(DuelBalanceReview).where(DuelBalanceReview.beatmap_id == beatmap_id,
                                                                                 DuelBalanceReview.decision.is_(None)))).all():
                review.decision, review.reviewer_id, review.decided_at = decision, actor, now
            _audit(session, actor, role, f"map_{decision.lower()}", str(beatmap_id), reason, before=before, after=after, now=now)
            return after

async def _within_stars(session, held, slot: Optional[str]) -> bool:
    from db.models.duel_catalog import DuelMapAnalysis
    from services.duels.features import FEATURE_VERSION
    from services.duels.generator import SLOT_MODS
    from services.duels.rules import in_catalogue

    if held is None or not held.revision_hash:
        return False
    rows = (await session.execute(select(DuelMapAnalysis.mods, DuelMapAnalysis.stars).where(
        DuelMapAnalysis.revision_hash == held.revision_hash, DuelMapAnalysis.feature_version == FEATURE_VERSION))).all()
    wanted = {"".join(SLOT_MODS[slot])} if slot in SLOT_MODS else {mods for mods, _ in rows}
    return any(in_catalogue(stars) for mods, stars in rows if mods in wanted)

NOT_ADDED = {"not_found": "MAP_NOT_FOUND", "not_standard": "NOT_STANDARD", "length": "MAP_LENGTH", "no_file": "NO_FILE",
             "parse_failed": "PARSE_FAILED", "recovering": "SERVICE_RECOVERING"}

async def add_map(factory, actor: int, beatmap_id: int, slot: Optional[str], reason: str, owners: tuple[int, ...], bring,
                  now: Optional[datetime] = None) -> dict[str, Any]:
    from db.models.duel_catalog import DuelMapAssignment
    from services.duels.rules import SLOTS, reserved

    async with factory() as session:
        await _allowed(session, actor, BALANCE_REVIEWER, owners)
    if slot is not None and slot not in SLOTS and not reserved(slot):
        raise catalog.CatalogRefusal("UNKNOWN_SLOT")
    outcome, entry = await bring(beatmap_id)
    if outcome in NOT_ADDED:
        raise catalog.CatalogRefusal(NOT_ADDED[outcome])
    status = (entry or {}).get("status")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            held = await session.get(DuelMapAssignment, beatmap_id)
            if held is not None and held.state == catalog.REJECTED:
                raise catalog.CatalogRefusal("REJECTED_STAYS")
            if not await _within_stars(session, held, slot):
                raise catalog.CatalogRefusal("STARS_RANGE")
            _audit(session, actor, role, "map_add", str(beatmap_id), reason, after={"slot": slot, "outcome": outcome, "status": status}, now=now)
            decision = "APPROVE" if held is None or held.slot in (None, slot) else "MOVE"
    if slot is not None:
        await review_map(factory, actor, beatmap_id, decision, reason, owners, slot, now)
    return {"outcome": outcome, "status": status}

async def note_discovery(factory, actor: int, least: float, most: float, reason: str, owners: tuple[int, ...] = (),
                         now: Optional[datetime] = None) -> None:
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            _audit(session, actor, role, "discover", f"{least:.1f}-{most:.1f}", reason.strip(), now=now)

async def discovery_result(factory, actor, slot, band, version, outcomes, owners=()):
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            _audit(session, actor, role, "discover_result", f"{slot}:{band}", "", after={"slot": slot, "band": band, "model": version, "outcomes": outcomes})


async def note_training(factory, actor: int, reason: str, owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> None:
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

async def find_player(factory, who: str) -> Optional[int]:
    from sqlalchemy import func

    who = who.strip()
    if not who:
        return None
    async with factory() as session:
        if who.isdigit():
            found = await session.scalar(select(Player.id).where(Player.osu_user_id == int(who)))
            if found is not None:
                return int(found)
        found = await session.scalar(select(Player.id).where(func.lower(Player.osu_username) == who.lower()))
        return int(found) if found is not None else None

async def revoke(factory, actor: int, player_id: int, role: str, reason: str, owners: tuple[int, ...] = (),
                 now: Optional[datetime] = None) -> None:
    if role not in ROLES or role == OWNER:
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            granted = await _allowed(session, actor, OWNER, owners)
            held = await session.get(DuelRole, (player_id, role))
            if held is None:
                raise DuelError("INVALID_STATE")
            await session.delete(held)
            _audit(session, actor, granted, "revoke", f"{player_id}:{role}", reason, now=now)

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
            if chosen is None or chosen.state not in ("shadow", "retired") or not _fits(chosen.body):
                raise DuelError("INVALID_STATE")
            for row in (await session.scalars(select(DuelModelVersion).where(DuelModelVersion.state == "published"))).all():
                row.state = "retired"
            chosen.state, chosen.published_at, chosen.published_by = "published", now, actor
            _audit(session, actor, role, "publish_model", version, reason, now=now)

async def delete_model(factory, actor: int, version: str, reason: str, owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> None:
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, MODEL_PUBLISHER, owners)
            chosen = await session.get(DuelModelVersion, version)
            if chosen is None or chosen.state == "published":
                raise DuelError("INVALID_STATE")
            await session.delete(chosen)
            _audit(session, actor, role, "delete_model", version, reason.strip(), before={"state": chosen.state}, now=now)

def _fits(body) -> bool:
    from services.duels import kamerton

    try:
        kamerton.checked(body)
    except kamerton.BROKEN:
        return False
    return True

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

async def working_model(factory):
    from services.duels import kamerton

    chosen = await published(factory)
    if chosen is None:
        return None
    try:
        return kamerton.checked(chosen.body)
    except kamerton.BROKEN:
        logger.warning("published duel slot model %s no longer fits the analyser; maps wait for reviewers", chosen.version)
        return None

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
                                      "label": corpus.read_back(labels.get(key + (None,)), row.original_slot), "maps": []})
        label, verified = importer.resolved(row, labels)
        cell["maps"].append({"beatmap_id": int(row.beatmap_id), "beatmapset_id": int(row.beatmapset_id) if row.beatmapset_id else None,
                             "label": label or corpus.EXCLUDED, "verified": verified, "file": row.revision_hash is not None,
                             "title": getattr(revisions.get(row.revision_hash), "title", None), "artist": getattr(revisions.get(row.revision_hash), "artist", None),
                             "version": getattr(revisions.get(row.revision_hash), "version", None), "mapper": getattr(revisions.get(row.revision_hash), "mapper", None),
                             "conflict": bool(row.conflict), "excluded": bool(row.excluded)})
    return list(cells.values())

async def label_corpus(factory, actor: int, corpus_version: str, year: int, stage: str, original_slot: str, beatmap_id: Optional[int],
                       label: str, reason: str, owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> dict[str, Any]:
    from db.models.duel_catalog import DuelCorpusLabel
    from services.duels import corpus

    now = now or utcnow()
    if not corpus.allowed(label) or corpus_version not in corpus.LEARNED:
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
    if not name:
        raise DuelError("INVALID_STATE")
    found = corpus.read_batch(text, year, f"own:{name}")
    if not found or year < 0 or year > 9999:
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, BALANCE_REVIEWER, owners)
            added = 0
            for example in corpus.distinct(found):
                label = corpus.label(example.original_slot)
                stage = name[:32]
                held = await session.scalar(select(DuelTrainingExample).where(
                    DuelTrainingExample.corpus_version == corpus.OWN_VERSION, DuelTrainingExample.year == year,
                    DuelTrainingExample.stage == stage, DuelTrainingExample.original_slot == example.original_slot,
                    DuelTrainingExample.beatmap_id == example.beatmap_id))
                if held is not None:
                    continue
                session.add(DuelTrainingExample(corpus_version=corpus.OWN_VERSION, source=f"own:{name}"[:256], year=year, stage=stage,
                                                original_slot=example.original_slot, beatmap_id=example.beatmap_id,
                                                beatmapset_id=example.beatmapset_id, normalized_label=label or corpus.EXCLUDED, conflict=False, fetched_at=now))
                if label is not None:
                    session.add(DuelCorpusLabel(corpus_version=corpus.OWN_VERSION, year=year, stage=stage, original_slot=example.original_slot,
                                                beatmap_id=example.beatmap_id, label=label, reviewer_id=actor, reason=reason.strip(), updated_at=now))
                added += 1
            _audit(session, actor, role, "add_tournament", name[:64], reason.strip(), after={"maps": added, "year": year}, now=now)
    return {"added": added, "read": len(found)}

async def batches(factory, actor: int, owners: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    from db.models.duel_catalog import DuelTrainingExample
    from services.duels import corpus, importer

    async with factory() as session:
        await _allowed(session, actor, OWNER, owners)
        labels = await importer.corpus_labels(session)
        rows = (await session.scalars(select(DuelTrainingExample).where(DuelTrainingExample.corpus_version == corpus.OWN_VERSION))).all()
        added = (await session.scalars(select(DuelAudit).where(DuelAudit.action == "add_tournament").order_by(DuelAudit.id))).all()
        found: dict[tuple[int, str], dict[str, Any]] = {}
        for row in rows:
            batch = found.setdefault((row.year, row.stage), {"name": row.stage, "year": row.year, "maps": 0, "labelled": 0, "excluded": 0,
                                                             "author": None, "added_at": None})
            batch["maps"] += 1
            batch["labelled"] += int(importer.resolved(row, labels)[1])
            batch["excluded"] += int(bool(row.excluded))
        for audit in added:
            year = (audit.after or {}).get("year")
            batch = found.get((year, (audit.target or "")[:32]))
            if batch is not None:
                batch["author"] = audit.actor_id
                batch["added_at"] = audit.created_at.isoformat() + "Z" if audit.created_at else None
        authors = {int(batch["author"]) for batch in found.values() if batch["author"] is not None}
        people = {row.id: row for row in (await session.scalars(select(Player).where(Player.id.in_(authors or {0})))).all()}
        for batch in found.values():
            held = people.get(batch["author"])
            batch["author"] = {"player": held.id, "name": held.osu_username, "osu_id": held.osu_user_id} if held is not None else None
        return sorted(found.values(), key=lambda batch: (batch["added_at"] or "", batch["year"], batch["name"]), reverse=True)

async def _batch_rows(session, name: str, year: int, beatmap_id: Optional[int]):
    from db.models.duel_catalog import DuelCorpusLabel, DuelTrainingExample
    from services.duels import corpus

    examples = select(DuelTrainingExample).where(DuelTrainingExample.corpus_version == corpus.OWN_VERSION,
                                                 DuelTrainingExample.year == year, DuelTrainingExample.stage == name)
    labels = select(DuelCorpusLabel).where(DuelCorpusLabel.corpus_version == corpus.OWN_VERSION,
                                           DuelCorpusLabel.year == year, DuelCorpusLabel.stage == name)
    if beatmap_id is not None:
        examples = examples.where(DuelTrainingExample.beatmap_id == beatmap_id)
        labels = labels.where(DuelCorpusLabel.beatmap_id == beatmap_id)
    found = (await session.scalars(examples.with_for_update())).all()
    if not found:
        raise DuelError("BATCH_NOT_FOUND")
    return found, (await session.scalars(labels.with_for_update())).all()

def _batch_target(name: str, year: int, beatmap_id: Optional[int]) -> str:
    return (f"{year}:{name}" + (f":{beatmap_id}" if beatmap_id is not None else ""))[:64]

async def rename_batch(factory, actor: int, name: str, year: int, new_name: str, new_year: int, reason: str,
                       owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> dict[str, Any]:
    from db.models.duel_catalog import DuelTrainingExample
    from services.duels import corpus

    stage = new_name.strip()[:32]
    if not stage or not 0 <= new_year <= 9999:
        raise DuelError("INVALID_STATE")
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, OWNER, owners)
            examples, labels = await _batch_rows(session, name, year, None)
            if (stage, new_year) != (name, year) and await session.scalar(select(DuelTrainingExample.id).where(
                    DuelTrainingExample.corpus_version == corpus.OWN_VERSION, DuelTrainingExample.year == new_year,
                    DuelTrainingExample.stage == stage).limit(1)) is not None:
                raise DuelError("BATCH_EXISTS")
            for row in examples:
                row.stage, row.year, row.source = stage, new_year, f"own:{new_name.strip()}"[:256]
            for row in labels:
                row.stage, row.year = stage, new_year
            after = {"name": stage, "year": new_year}
            _audit(session, actor, role, "batch_rename", _batch_target(name, year, None), reason.strip(),
                   before={"name": name, "year": year}, after=after, now=now)
            return after

async def delete_batch(factory, actor: int, name: str, year: int, beatmap_id: Optional[int], reason: str,
                       owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> dict[str, Any]:
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, OWNER, owners)
            examples, labels = await _batch_rows(session, name, year, beatmap_id)
            for row in list(examples) + list(labels):
                await session.delete(row)
            after = {"maps": len(examples)}
            _audit(session, actor, role, "batch_delete", _batch_target(name, year, beatmap_id), reason.strip(), after=after, now=now)
            return after

async def exclude_batch(factory, actor: int, name: str, year: int, beatmap_id: Optional[int], excluded: bool, reason: str,
                        owners: tuple[int, ...] = (), now: Optional[datetime] = None) -> dict[str, Any]:
    async with factory() as session:
        async with session.begin():
            role = await _allowed(session, actor, OWNER, owners)
            examples, _ = await _batch_rows(session, name, year, beatmap_id)
            for row in examples:
                row.excluded = excluded
            after = {"maps": len(examples), "excluded": excluded}
            _audit(session, actor, role, "batch_exclude" if excluded else "batch_include", _batch_target(name, year, beatmap_id),
                   reason.strip(), after=after, now=now)
            return after

SYSTEM = "SYSTEM"

async def note_hunt(factory, found: dict[str, Any], now: Optional[datetime] = None) -> None:
    async with factory() as session:
        async with session.begin():
            _audit(session, 0, SYSTEM, "auto_discover", f"{found['slot']}:{found['band']}", found["mode"], after=found, now=now)
