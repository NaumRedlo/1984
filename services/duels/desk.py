import os
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import func, select

from db.models.duel import (Duel, DuelAudit, DuelBalanceReview, DuelDeadline, DuelEvent, DuelFeedback, DuelModelVersion, DuelParticipant, DuelPool,
                            DuelPoolEntry, DuelRole, DuelRoom, DuelRound)
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from db.models.player import Player
from services.duels import balance, catalog, importer, states
from services.duels.features import FEATURE_VERSION
from services.duels.generator import SLOT_MODS
from services.duels.rules import SLOTS, RESERVE_SLOTS, STARS_MOST, in_catalogue, reserved
from utils.timeutils import utcnow

BANDS = (5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5)
CATALOG_SLOTS = tuple(sorted((*SLOTS, *RESERVE_SLOTS), key=lambda slot: (("NM", "HD", "HR", "DT", "FM", "EZ", "FL", "TB").index(slot[:2]), slot)))
ENOUGH = 5
STUCK_AFTER = timedelta(minutes=5)
WAITING_STATES = frozenset({states.PAUSED, states.RECOVERING, states.VERIFYING, states.ABORTING, states.ROOM_SETUP, states.WAITING_ROOM})
RECENT = timedelta(hours=6)
QUEUE_MOST = 100
CHUNK = 500

def _stamp(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value is not None else None

def band_of(stars: float) -> int:
    found = 0
    for index, edge in enumerate(BANDS):
        if stars >= edge:
            found = index
    return found

def discovery_range(band: int) -> tuple[float, float]:
    if type(band) is not int or not 0 <= band < len(BANDS):
        raise ValueError("band")
    low = BANDS[band]
    return low, BANDS[band + 1] if band + 1 < len(BANDS) else STARS_MOST

async def _any_role(session, actor: int, owners: tuple[int, ...]) -> set[str]:
    held = await balance.roles_of(session, actor, owners)
    if not held:
        raise balance.Forbidden(balance.BALANCE_REVIEWER)
    return held

async def _names(session, player_ids) -> dict[int, str]:
    ids = {int(value) for value in player_ids if value is not None}
    if not ids:
        return {}
    rows = (await session.execute(select(Player.id, Player.osu_username).where(Player.id.in_(ids)))).all()
    return {int(row[0]): row[1] for row in rows}

async def _faces(session, player_ids) -> dict[int, int]:
    ids = {int(value) for value in player_ids if value is not None}
    if not ids:
        return {}
    rows = (await session.execute(select(Player.id, Player.osu_user_id).where(Player.id.in_(ids)))).all()
    return {int(row[0]): int(row[1]) for row in rows if row[1] is not None}

async def my_roles(factory, actor: int, owners: tuple[int, ...] = ()) -> list[str]:
    async with factory() as session:
        return sorted(await balance.roles_of(session, actor, owners))

def _chunks(values) -> list[list[Any]]:
    ordered = sorted(values)
    return [ordered[at:at + CHUNK] for at in range(0, len(ordered), CHUNK)]

async def _latest(session, columns, wanted) -> dict[tuple[str, str], Any]:
    pairs = {(digest, mods) for digest, mods in wanted if digest}
    found: dict[tuple[str, str], Any] = {}
    for digests in _chunks({digest for digest, _ in pairs}):
        rows = (await session.execute(select(DuelMapAnalysis.revision_hash, DuelMapAnalysis.mods, *columns)
                                      .where(DuelMapAnalysis.revision_hash.in_(digests), DuelMapAnalysis.mods.in_({mods for _, mods in pairs}),
                                             DuelMapAnalysis.feature_version == FEATURE_VERSION)
                                      .order_by(DuelMapAnalysis.created_at, DuelMapAnalysis.id))).all()
        for row in rows:
            if (row[0], row[1]) in pairs:
                found[(row[0], row[1])] = row[2]
    return found

async def _analyses(session, wanted) -> dict[tuple[str, str], DuelMapAnalysis]:
    return await _latest(session, (DuelMapAnalysis,), wanted)

async def _stars(session, wanted) -> dict[tuple[str, str], Optional[float]]:
    return await _latest(session, (DuelMapAnalysis.stars,), wanted)

async def _analysis(session, digest: Optional[str], mods: str) -> Optional[DuelMapAnalysis]:
    return (await _analyses(session, [(digest, mods)])).get((digest, mods))

async def _feedback(session, beatmap_ids) -> dict[int, list[DuelFeedback]]:
    found: dict[int, list[DuelFeedback]] = {}
    for ids in _chunks(set(beatmap_ids)):
        for row in (await session.scalars(select(DuelFeedback).where(DuelFeedback.beatmap_id.in_(ids)).order_by(DuelFeedback.id))).all():
            found.setdefault(int(row.beatmap_id), []).append(row)
    return found

async def _played(session, beatmap_ids) -> dict[int, int]:
    found: dict[int, int] = {}
    for ids in _chunks(set(beatmap_ids)):
        rows = (await session.execute(select(DuelPoolEntry.beatmap_id, func.count(DuelPoolEntry.id))
                                      .where(DuelPoolEntry.beatmap_id.in_(ids), DuelPoolEntry.used.is_(True))
                                      .group_by(DuelPoolEntry.beatmap_id))).all()
        found.update({int(beatmap): int(count) for beatmap, count in rows})
    return found

async def _opinions(session, beatmap_id: int) -> dict[str, Any]:
    return _opinion((await _feedback(session, [beatmap_id])).get(beatmap_id, []), (await _played(session, [beatmap_id])).get(beatmap_id, 0))

def _opinion(rows: list[DuelFeedback], played: int) -> dict[str, Any]:
    difficulty: dict[str, int] = {}
    fits = {"fits": 0, "off": 0}
    for row in rows:
        if row.fit_label in fits:
            fits[row.fit_label] += 1
        if row.difficulty_label:
            difficulty[row.difficulty_label] = difficulty.get(row.difficulty_label, 0) + 1
    return {"played": played, "fits": fits["fits"], "off": fits["off"], "difficulty": difficulty,
            "comments": [row.comment for row in rows if row.comment][-5:]}

def _explain(model, features: dict[str, Any], base_ar: float, hr_cs: float, views: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    if model is None or not features:
        return None
    try:
        prediction = model.predict(features, base_ar, hr_cs, views)
    except Exception:
        return None
    ranked = sorted(((slot, value) for slot, value in prediction.probabilities.items() if slot != "OTHER"), key=lambda pair: -pair[1])[:4]
    return {"slot": prediction.slot, "probability": prediction.probability, "margin": prediction.margin,
            "top": [{"slot": slot, "probability": value} for slot, value in ranked],
            "reasons": [{"feature": name, "weight": weight, **({"level": prediction.levels[name]} if name in prediction.levels else {})}
                        for name, weight in prediction.reasons],
            "outside": prediction.outside, "version": model.version}

SETTINGS = {"CircleSize": "cs", "ApproachRate": "ar", "OverallDifficulty": "od", "HPDrainRate": "hp"}

def settings_of(digest: Optional[str], folder: Optional[str] = None) -> dict[str, float]:
    if not digest:
        return {}
    if folder is None:
        from config.settings import DUEL_OSU_FOLDER

        folder = DUEL_OSU_FOLDER
    found: dict[str, float] = {}
    try:
        with open(os.path.join(folder, f"{digest}.osu"), encoding="utf-8-sig", errors="replace") as handle:
            inside = False
            for line in handle:
                line = line.strip()
                if line.startswith("["):
                    if inside:
                        break
                    inside = line == "[Difficulty]"
                    continue
                name, _, value = line.partition(":")
                if inside and name.strip() in SETTINGS:
                    try:
                        found[SETTINGS[name.strip()]] = float(value.strip())
                    except ValueError:
                        pass
    except OSError:
        return {}
    if "ar" not in found and "od" in found:
        found["ar"] = found["od"]
    return found

def bpm_of(digest: Optional[str], folder: Optional[str] = None) -> Optional[float]:
    if not digest:
        return None
    if folder is None:
        from config.settings import DUEL_OSU_FOLDER
        folder = DUEL_OSU_FOLDER
    import math
    from collections import defaultdict
    timing, last, section = [], 0.0, ""
    try:
        with open(os.path.join(folder, f"{digest}.osu"), encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("["):
                    section = line
                    continue
                values = line.split(",")
                try:
                    if section == "[TimingPoints]" and len(values) >= 2:
                        time, beat = float(values[0]), float(values[1])
                        red = len(values) < 7 or values[6] == "1"
                        if red and math.isfinite(time) and math.isfinite(beat) and beat > 0:
                            timing.append((time, beat))
                    elif section == "[HitObjects]" and len(values) >= 3:
                        time = float(values[2])
                        if math.isfinite(time):
                            last = max(last, time)
                except ValueError:
                    continue
    except OSError:
        return None
    if not timing:
        return None
    timing.sort()
    durations = defaultdict(float)
    for at, (time, beat) in enumerate(timing):
        end = timing[at + 1][0] if at + 1 < len(timing) else last
        durations[beat] += max(0.0, min(end, last) - max(0.0, time))
    return 60000.0 / max(durations, key=durations.get)

async def _revisions(session, beatmap_ids) -> dict[int, DuelMapRevision]:
    found: dict[int, DuelMapRevision] = {}
    for ids in _chunks(set(beatmap_ids)):
        for row in (await session.scalars(select(DuelMapRevision).where(DuelMapRevision.beatmap_id.in_(ids))
                                          .order_by(DuelMapRevision.downloaded_at, DuelMapRevision.id))).all():
            found[int(row.beatmap_id)] = row
    return found

async def _maps(session, wanted: list[tuple[int, Optional[str], Optional[str]]], model) -> list[dict[str, Any]]:
    revisions = await _revisions(session, [beatmap_id for beatmap_id, _, _ in wanted])
    digests = [digest or (revisions[beatmap_id].hash if beatmap_id in revisions else None) for beatmap_id, digest, _ in wanted]
    needed = {(digest, mods) for digest in digests for mods in (importer.SEEN_AS, *importer.VIEWED)}
    needed |= {(digest, "".join(SLOT_MODS[slot])) for digest, (_, _, slot) in zip(digests, wanted) if slot in SLOT_MODS}
    found = await _analyses(session, needed)
    return [_described(beatmap_id, digest, revisions.get(beatmap_id), slot, found, model)
            for (beatmap_id, _, slot), digest in zip(wanted, digests)]

async def _map(session, beatmap_id: int, digest: Optional[str], model, slot: Optional[str] = None) -> dict[str, Any]:
    return (await _maps(session, [(beatmap_id, digest, slot)], model))[0]

def _described(beatmap_id: int, digest: Optional[str], revision: Optional[DuelMapRevision], slot: Optional[str],
               found: dict[tuple[str, str], DuelMapAnalysis], model) -> dict[str, Any]:
    plain = found.get((digest, "NF"))
    rock = found.get((digest, "NFHR"))
    features = plain.features if plain is not None else {}
    effective = (features or {}).get("effective") or {}
    rock_cs = (((rock.features or {}).get("effective") or {}).get("cs") if rock is not None else None) or effective.get("cs", 0.0)
    mods = [mod for mod in SLOT_MODS.get(slot or "", ()) if mod != "NF"]
    slotted = found.get((digest, "".join(SLOT_MODS[slot]))) if mods else None
    settings = settings_of(digest)
    played = (slotted.features or {}) if slotted is not None else features or {}
    if slotted is not None:
        settings.update({name: value for name, value in (played.get("effective") or {}).items() if name in ("cs", "ar", "od")})
        if "HR" in mods and "hp" in settings:
            settings["hp"] = min(10.0, settings["hp"] * 1.4)
    bpm = bpm_of(digest)
    if bpm is not None and ("DT" in mods or "NC" in mods):
        bpm *= 1.5
    views = {mods_: row.features for mods_ in importer.VIEWED if (row := found.get((digest, mods_))) is not None and row.features}
    return {"beatmap_id": beatmap_id, "beatmapset_id": int(revision.beatmapset_id) if revision is not None and revision.beatmapset_id else None,
            "mapper": revision.mapper if revision is not None else None, "ranked_status": revision.ranked_status if revision is not None else None,
            "title": revision.title if revision is not None else None, "artist": revision.artist if revision is not None else None,
            "version": revision.version if revision is not None else None,
            "tags": {key: (revision.tags or {}).get(key) or [] for key in ("tournaments", "stages", "slots")} if revision is not None and revision.tags else None,
            "stars": (slotted or plain).stars if (slotted or plain) is not None else None, "plain_stars": plain.stars if plain is not None else None,
            "mods": mods, "revision": digest, "settings": settings, "length": played.get("length_seconds"), "bpm": bpm,
            "model": _explain(model, features, effective.get("ar", 0.0), rock_cs, views)}

def _graded(assignment: Optional[DuelMapAssignment]) -> dict[str, Any]:
    return {"tier": assignment.tier if assignment is not None else None, "reasons": list((assignment.reasons if assignment is not None else None) or [])}

async def queue(factory, actor: int, owners: tuple[int, ...] = (), model=None) -> list[dict[str, Any]]:
    async with factory() as session:
        await balance._allowed(session, actor, balance.BALANCE_REVIEWER, owners)
        found: list[tuple[dict[str, Any], int, Optional[str], Optional[str]]] = []
        reviews = (await session.scalars(select(DuelBalanceReview).where(DuelBalanceReview.decision.is_(None))
                                         .order_by(DuelBalanceReview.requested_at).limit(QUEUE_MOST))).all()
        held = {int(row.beatmap_id): row for row in (await session.scalars(
            select(DuelMapAssignment).where(DuelMapAssignment.beatmap_id.in_({int(review.beatmap_id) for review in reviews})))).all()} if reviews else {}
        seen = set()
        for review in reviews:
            assignment = held.get(int(review.beatmap_id))
            seen.add(int(review.beatmap_id))
            slot = assignment.slot if assignment is not None else None
            found.append(({"kind": "review", "id": review.id, "reason": review.reason, "since": _stamp(review.requested_at),
                           "slot": slot, "secondary": catalog.secondary_of(assignment), **_graded(assignment),
                           "state": assignment.state if assignment is not None else None, "opinions": True, "feedback": True},
                          int(review.beatmap_id), review.revision_hash, slot))
        current = (DuelMapRevision.beatmap_id == DuelMapAssignment.beatmap_id) & (DuelMapRevision.hash == DuelMapAssignment.revision_hash)
        unsure = (await session.execute(select(DuelMapAssignment, DuelMapRevision).join(DuelMapRevision, current, isouter=True)
                                        .where(DuelMapAssignment.state == catalog.AUTO_ASSIGNED, DuelMapAssignment.admitted.is_(True))
                                        .order_by(DuelMapAssignment.confidence, DuelMapAssignment.margin))).all()
        for assignment, revision in sorted(unsure, key=lambda row: not (row[1] is not None and catalog.doubted(row[1]))):
            if len(found) >= QUEUE_MOST:
                break
            if int(assignment.beatmap_id) in seen or (revision is not None and catalog.barred(revision)):
                continue
            doubted = revision is not None and catalog.doubted(revision)
            if not doubted and catalog.confidence_level(assignment.confidence, assignment.margin) == "confident":
                continue
            seen.add(int(assignment.beatmap_id))
            found.append(({"kind": "review", "id": None, "reason": "doubted" if doubted else "uncertain", "since": _stamp(assignment.updated_at), "slot": assignment.slot,
                           **_graded(assignment), "state": assignment.state, "opinions": True, "feedback": None},
                          int(assignment.beatmap_id), assignment.revision_hash, assignment.slot))
        waiting = (await session.execute(select(DuelMapAssignment, DuelMapRevision).join(DuelMapRevision, current, isouter=True)
            .where(DuelMapAssignment.state == catalog.UNASSIGNED, DuelMapAssignment.admitted.is_(True))
            .order_by(DuelMapAssignment.updated_at))).all()
        waiting = [row for row in waiting if row[1] is None or not catalog.barred(row[1])]
        waiting = [assignment for assignment, _ in sorted(waiting, key=lambda row: not ((row[1].tags if row[1] is not None else None) or {}).get("tournaments"))][:max(0, QUEUE_MOST - len(found))]
        for assignment in waiting:
            if int(assignment.beatmap_id) in seen:
                continue
            found.append(({"kind": "unassigned", "id": None, "reason": "unassigned", "since": _stamp(assignment.updated_at), "slot": None,
                           "state": assignment.state, "opinions": None, "feedback": None},
                          int(assignment.beatmap_id), assignment.revision_hash, None))
        maps = await _maps(session, [(beatmap, digest, slot) for _, beatmap, digest, slot in found], model)
        heard = [beatmap for task, beatmap, _, _ in found if task["opinions"]]
        feedback, played, now = await _feedback(session, heard), await _played(session, heard), utcnow()
        for (task, beatmap, _, _), described in zip(found, maps):
            task["map"] = described
            if task["opinions"]:
                task["opinions"] = _opinion(feedback.get(beatmap, []), played.get(beatmap, 0))
            if task["feedback"]:
                task["feedback"] = balance.weighed(feedback.get(beatmap, []), now)
        return [task for task, _, _, _ in found]

async def coverage(factory, actor: int, owners: tuple[int, ...] = ()) -> dict[str, Any]:
    async with factory() as session:
        await balance._allowed(session, actor, balance.BALANCE_REVIEWER, owners)
        return await counted(session)

def catalogue_level(assignment, revision):
    if reserved(assignment.slot or ""):
        if assignment.state == catalog.HUMAN_APPROVED and assignment.admitted and revision is not None and revision.status == "ok" and not catalog.barred(revision):
            return "human"
        return None
    return catalog.playable(assignment, revision)


def catalogue_mods(slot):
    return "".join(SLOT_MODS[slot]) if slot in SLOT_MODS else importer.GROUP_MODS[slot[:2]]


async def counted(session) -> dict[str, Any]:
    rows = (await session.execute(
        select(DuelMapAssignment, DuelMapRevision)
        .join(DuelMapRevision, (DuelMapRevision.beatmap_id == DuelMapAssignment.beatmap_id)
              & (DuelMapRevision.hash == DuelMapAssignment.revision_hash), isouter=True)
    )).all()
    slots = list(CATALOG_SLOTS)
    slots.extend(sorted({assignment.slot for assignment, _ in rows if reserved(assignment.slot or "")} - set(slots)))
    grid = {slot: [0] * len(BANDS) for slot in slots}
    extra = {slot: [0] * len(BANDS) for slot in slots}
    totals = {"playable": 0, "human": 0, "uncertain": 0, "quarantined": 0, "unassigned": 0}
    listed = []
    for assignment, revision in rows:
        if assignment.state == catalog.QUARANTINED:
            totals["quarantined"] += 1
            continue
        if assignment.state == catalog.UNASSIGNED:
            totals["unassigned"] += 1
            continue
        level = catalogue_level(assignment, revision)
        if level is None:
            continue
        secondary = catalog.secondary_of(assignment) if assignment.state == catalog.HUMAN_APPROVED else None
        listed.append((assignment, revision, level, secondary if secondary in extra else None))
    stars = await _stars(session, [(revision.hash, catalogue_mods(slot)) for assignment, revision, _, secondary in listed
                                   for slot in (assignment.slot, secondary) if slot is not None])
    for assignment, revision, level, secondary in listed:
        own = stars.get((revision.hash, catalogue_mods(assignment.slot)))
        if not in_catalogue(own):
            continue
        grid[assignment.slot][band_of(float(own))] += 1
        totals["playable"] += 1
        if level == "human":
            totals["human"] += 1
        elif level == "uncertain":
            totals["uncertain"] += 1
        if secondary is not None:
            held = stars.get((revision.hash, catalogue_mods(secondary)))
            if in_catalogue(held):
                extra[secondary][band_of(float(held))] += 1
    return {"bands": list(BANDS), "slots": slots, "grid": grid, "secondary": extra, "totals": totals}

async def inspect_map(factory, actor: int, beatmap_id: int, owners: tuple[int, ...] = ()) -> Optional[dict[str, Any]]:
    async with factory() as session:
        await balance._allowed(session, actor, balance.BALANCE_REVIEWER, owners)
        assignment = await session.get(DuelMapAssignment, beatmap_id)
        if assignment is None:
            return None
        model = await balance.working_model(factory)
        return {"kind": "review", "id": None, "reason": "manual", "slot": assignment.slot, "secondary": catalog.secondary_of(assignment),
                **_graded(assignment), "state": assignment.state, "map": await _map(session, beatmap_id, assignment.revision_hash, model, assignment.slot),
                "opinions": await _opinions(session, beatmap_id)}

def _typical(model, slot: str, digest: str, found: dict[tuple[str, str], DuelMapAnalysis]) -> Optional[float]:
    plain = found.get((digest, importer.SEEN_AS))
    if model is None or plain is None or not plain.features:
        return None
    rock = found.get((digest, "NFHR"))
    effective = plain.features.get("effective") or {}
    rock_cs = (((rock.features or {}).get("effective") or {}).get("cs") if rock is not None else None) or effective.get("cs", 0.0)
    views = {mods: row.features for mods in importer.VIEWED if (row := found.get((digest, mods))) is not None and row.features}
    try:
        return model.predict(plain.features, effective.get("ar", 0.0), rock_cs, views).probabilities.get(slot)
    except Exception:
        return None

TIER_ORDER = {catalog.EXEMPLARY: 0, catalog.FITS: 1, None: 1, catalog.BORDERLINE: 2}

async def coverage_cell(factory, actor: int, slot: str, band: int, owners: tuple[int, ...] = (), model=None) -> list[dict[str, Any]]:
    async with factory() as session:
        await balance._allowed(session, actor, balance.BALANCE_REVIEWER, owners)
        rows = (await session.execute(
            select(DuelMapAssignment, DuelMapRevision)
            .join(DuelMapRevision, (DuelMapRevision.beatmap_id == DuelMapAssignment.beatmap_id)
                  & (DuelMapRevision.hash == DuelMapAssignment.revision_hash))
            .where(DuelMapAssignment.slot == slot)
        )).all()
        names = await _names(session, [assignment.reviewer_id for assignment, _ in rows if assignment.reviewer_id is not None])
        listed = [(assignment, revision, level) for assignment, revision in rows if (level := catalogue_level(assignment, revision)) is not None]
        stars = await _stars(session, [(revision.hash, catalogue_mods(slot)) for _, revision, _ in listed])
        listed = [(assignment, revision, level) for assignment, revision, level in listed
                  if in_catalogue(own := stars.get((revision.hash, catalogue_mods(slot)))) and band_of(float(own)) == band]
        seen = await _analyses(session, {(revision.hash, mods) for _, revision, _ in listed for mods in (importer.SEEN_AS, *importer.VIEWED)}) if model is not None else {}
        found = []
        for assignment, revision, level in listed:
            own = stars[(revision.hash, catalogue_mods(slot))]
            found.append({"beatmap_id": int(assignment.beatmap_id), "beatmapset_id": int(revision.beatmapset_id) if revision.beatmapset_id else None,
                          "tier": assignment.tier, "typical": _typical(model, slot, revision.hash, seen),
                          "stars": float(own), "level": level, "mapper": revision.mapper, "title": revision.title,
                          "version": revision.version, "reviewer": names.get(assignment.reviewer_id)})
        return sorted(found, key=lambda item: (TIER_ORDER.get(item["tier"], 1), -(item["typical"] or 0.0), item["stars"]))

async def _stuck_detail(session, duel: Duel) -> dict[str, Any]:
    room = await session.scalar(select(DuelRoom).where(DuelRoom.duel_id == duel.id).order_by(DuelRoom.id.desc()).limit(1))
    played = await session.scalar(select(DuelRound).where(DuelRound.duel_id == duel.id).order_by(DuelRound.ordinal.desc()).limit(1))
    beatmap, title = None, None
    if played is not None:
        entry = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                     .where(DuelPool.duel_id == duel.id, DuelPoolEntry.slot == played.slot).limit(1))
        if entry is not None:
            beatmap = int(entry.beatmap_id)
            revision = await session.scalar(select(DuelMapRevision).where(DuelMapRevision.hash == entry.revision_hash).limit(1))
            title = revision.title if revision is not None else None
    events = (await session.scalars(select(DuelEvent).where(DuelEvent.duel_id == duel.id)
                                    .order_by(DuelEvent.sequence.desc()).limit(8))).all()
    deadline = await session.scalar(select(DuelDeadline).where(DuelDeadline.duel_id == duel.id, DuelDeadline.processed_at.is_(None))
                                    .order_by(DuelDeadline.expires_at).limit(1))
    return {
        "room": {"match_id": room.match_id, "name": room.room_name, "status": room.status} if room is not None else None,
        "round": {"ordinal": played.ordinal, "slot": played.slot, "state": played.state, "beatmap_id": beatmap, "title": title}
                 if played is not None else None,
        "events": [{"type": event.type, "reason": (event.payload or {}).get("reason") or (event.payload or {}).get("to"),
                    "at": _stamp(event.occurred_at)} for event in events],
        "deadline": {"kind": deadline.kind, "at": _stamp(deadline.expires_at)} if deadline is not None else None,
    }

async def stuck(factory, actor: int, owners: tuple[int, ...] = (), now: Optional[datetime] = None,
                present: Optional[Callable[[int], bool]] = None) -> list[dict[str, Any]]:
    now = now or utcnow()
    async with factory() as session:
        await balance._allowed(session, actor, balance.TECHNICAL_OPERATOR, owners)
        rows = (await session.scalars(select(Duel).where(Duel.state.in_(WAITING_STATES), Duel.updated_at <= now - STUCK_AFTER)
                                      .order_by(Duel.updated_at))).all()
        found = []
        for duel in rows:
            people = (await session.scalars(select(DuelParticipant).where(DuelParticipant.duel_id == duel.id))).all()
            names = await _names(session, [person.player_id for person in people])
            found.append({"id": duel.id, "state": duel.state, "reason": duel.pause_reason or duel.state.lower(), "since": _stamp(duel.updated_at),
                          "wins": {"A": duel.wins_a, "B": duel.wins_b}, "mode": duel.mode,
                          "players": [{"player": person.player_id, "side": person.side, "name": names.get(person.player_id),
                                       "osu_id": int(person.osu_user_id) if person.osu_user_id else None,
                                       "present": present(person.player_id) if present is not None else None} for person in people],
                          **await _stuck_detail(session, duel)})
        return found

async def journal(factory, actor: int, owners: tuple[int, ...] = (), before: Optional[int] = None, limit: int = 50) -> list[dict[str, Any]]:
    async with factory() as session:
        await _any_role(session, actor, owners)
        query = select(DuelAudit).order_by(DuelAudit.id.desc()).limit(max(1, min(limit, 200)))
        if before is not None:
            query = query.where(DuelAudit.id < before)
        rows = (await session.scalars(query)).all()
        names = await _names(session, [row.actor_id for row in rows])
        faces = await _faces(session, [row.actor_id for row in rows])
        return [{"id": row.id, "actor": row.actor_id, "name": names.get(row.actor_id), "osu_id": faces.get(row.actor_id), "role": row.role, "action": row.action,
                 "target": row.target, "reason": row.reason, "before": row.before, "after": row.after, "at": _stamp(row.created_at)} for row in rows]

async def people(factory, actor: int, owners: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    async with factory() as session:
        await _any_role(session, actor, owners)
        rows = (await session.scalars(select(DuelRole).order_by(DuelRole.player_id, DuelRole.role))).all()
        names = await _names(session, [row.player_id for row in rows])
        faces = await _faces(session, [row.player_id for row in rows])
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            entry = grouped.setdefault(int(row.player_id), {"player": int(row.player_id), "name": names.get(int(row.player_id)),
                                                            "osu_id": faces.get(int(row.player_id)), "roles": []})
            entry["roles"].append(row.role)
        return list(grouped.values())

async def models(factory, actor: int, owners: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    async with factory() as session:
        held = await _any_role(session, actor, owners)
        if not held & {balance.BALANCE_REVIEWER, balance.MODEL_PUBLISHER, balance.OWNER}:
            raise balance.Forbidden(balance.MODEL_PUBLISHER)
        rows = (await session.scalars(select(DuelModelVersion).order_by(DuelModelVersion.created_at.desc()).limit(20))).all()
        return [{"version": row.version, "state": row.state, "labels": row.labels, "report": row.report, "created_at": _stamp(row.created_at),
                 "published_at": _stamp(row.published_at), "published_by": row.published_by} for row in rows]

async def training_job(factory, actor: int, name: str, owners: tuple[int, ...] = ()) -> Optional[dict[str, Any]]:
    from services.duels import training

    async with factory() as session:
        await balance._allowed(session, actor, balance.BALANCE_REVIEWER, owners)
    return training.snapshot(name)

async def live(factory, now: Optional[datetime] = None, watchers: Optional[Callable[[str], int]] = None) -> dict[str, Any]:
    now = now or utcnow()
    hidden = states.TERMINAL | states.UNCOMMITTED
    async with factory() as session:
        running = (await session.scalars(select(Duel).where(Duel.state.not_in(hidden)).order_by(Duel.created_at.desc()).limit(30))).all()
        done = (await session.scalars(select(Duel).where(Duel.state == states.COMPLETED, Duel.finalized_at >= now - RECENT)
                                      .order_by(Duel.finalized_at.desc()).limit(20))).all()
        people = (await session.scalars(select(DuelParticipant).where(DuelParticipant.duel_id.in_([duel.id for duel in running + done] or [""])))).all()
        names = await _names(session, [person.player_id for person in people])
        by_duel: dict[str, list[DuelParticipant]] = {}
        for person in people:
            by_duel.setdefault(person.duel_id, []).append(person)

        async def entry(duel: Duel, current: bool) -> dict[str, Any]:
            found = {"id": duel.id, "mode": duel.mode, "state": duel.state, "wins": {"A": duel.wins_a, "B": duel.wins_b},
                     "players": [{"player": person.player_id, "side": person.side, "name": names.get(person.player_id), "osu_id": person.osu_user_id}
                                 for person in sorted(by_duel.get(duel.id, []), key=lambda person: person.side or "")]}
            if current:
                found["watching"] = watchers(duel.id) if watchers is not None else 0
                round_ = await session.scalar(select(DuelRound).where(DuelRound.duel_id == duel.id).order_by(DuelRound.ordinal.desc()))
                found["slot"] = round_.slot if round_ is not None else None
                if round_ is not None:
                    entry_ = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                                  .where(DuelPool.duel_id == duel.id, DuelPoolEntry.slot == round_.slot))
                    found["beatmap_id"] = int(entry_.beatmap_id) if entry_ is not None else None
                    found["stars"] = (entry_.metrics_snapshot or {}).get("stars") if entry_ is not None else None
                    revision = await session.scalar(select(DuelMapRevision).where(DuelMapRevision.beatmap_id == entry_.beatmap_id,
                                                                                 DuelMapRevision.hash == entry_.revision_hash)) if entry_ is not None else None
                    found["title"] = revision.title if revision is not None else None
                    analysis = await _analysis(session, entry_.revision_hash, "".join(SLOT_MODS.get(round_.slot, ()))) if entry_ is not None else None
                    features = (analysis.features or {}) if analysis is not None else {}
                    length, rate = features.get("length_seconds"), (features.get("effective") or {}).get("rate", 1.0)
                    found["song_seconds"] = round(length * rate, 3) if length is not None else None
            else:
                found["finished_at"] = _stamp(duel.finalized_at)
                found["seconds_ago"] = max(0, int((now - duel.finalized_at).total_seconds())) if duel.finalized_at else None
                found["winner"] = duel.winner_id
            return found

        return {"running": [await entry(duel, True) for duel in running], "finished": [await entry(duel, False) for duel in done]}
