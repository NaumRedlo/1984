import asyncio
import os
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from db.models.duel_catalog import DuelCorpusLabel, DuelMapAnalysis, DuelMapAssignment, DuelMapRevision, DuelTrainingExample
from services.duels import catalog, corpus, mirrors
from services.duels.features import FEATURE_VERSION, analyse, apply_mods
from services.duels.generator import SLOT_MODS
from services.duels.osu_file import OsuFileError, parse
from services.duels.slot_model import OTHER, SlotModel, _softmax, report as scored, seen_vector
from utils.timeutils import utcnow

MOD_BITS = {"NF": 1, "HD": 8, "HR": 16, "DT": 64}
ANALYSED = tuple(sorted({"".join(mods) for mods in SLOT_MODS.values()}))
ENGINE_VERSION = "osu-api-attributes"
SHORTEST = 60
LONGEST = 600

def bits(mods: str) -> int:
    return sum(MOD_BITS[mods[i:i + 2]] for i in range(0, len(mods), 2) if mods[i:i + 2] in MOD_BITS)

def suitable(entry: dict[str, Any]) -> bool:
    return entry.get("status") == "ranked" and SHORTEST <= int(entry.get("length") or 0) <= LONGEST

WIKI = "https://raw.githubusercontent.com/ppy/osu-wiki/master/wiki/Tournaments/OWC/{year}/en.md"
YEARS = (2021, 2022, 2023, 2024, 2025)
SEEN_AS = "NF"
VIEWED = ("NFDT", "NFHR")

async def seen_of(session, digest: Optional[str]) -> tuple[Optional[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = (await session.scalars(select(DuelMapAnalysis).where(DuelMapAnalysis.revision_hash == digest,
                                                                DuelMapAnalysis.mods.in_((SEEN_AS,) + VIEWED),
                                                                DuelMapAnalysis.feature_version == FEATURE_VERSION))).all() if digest else []
    found = {row.mods: row.features for row in rows if row.features}
    return found.get(SEEN_AS), {mods: found[mods] for mods in VIEWED if mods in found}
GROUP_MODS = {"NM": "NF", "FM": "NF", "TB": "NF", "EZ": "NF", "FL": "NF", "HD": "NFHD", "HR": "NFHR", "DT": "NFDT"}

async def known(factory, entry: dict[str, Any]) -> bool:
    async with factory() as session:
        found = await session.scalar(select(DuelMapAssignment.beatmap_id).where(
            DuelMapAssignment.beatmap_id == entry["beatmap_id"], DuelMapAssignment.revision_hash == (entry.get("checksum") or "")))
    return found is not None

async def _analysed(session, digest: str) -> set[str]:
    return set((await session.scalars(select(DuelMapAnalysis.mods).where(DuelMapAnalysis.revision_hash == digest,
                                                                        DuelMapAnalysis.feature_version == FEATURE_VERSION))).all())

async def _stars(osu, beatmap_id: int, mods: str, nominal: float) -> Optional[float]:
    wanted = bits(mods) & ~MOD_BITS["NF"]
    if not wanted:
        return nominal
    if osu is None:
        return None
    try:
        found = await osu.get_beatmap_attributes(beatmap_id, wanted)
    except Exception:
        return None
    value = (found or {}).get("star_rating")
    return float(value) if value is not None else None

async def ingest(factory, fetch, osu, entry: dict[str, Any], folder: str, model=None,
                 now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    if await known(factory, entry):
        return "known"
    fetched = await mirrors.fetch_osu(fetch, entry["beatmapset_id"], entry["beatmap_id"], entry.get("checksum"))
    if fetched is None:
        return "no_file"
    source, raw = fetched
    digest = await asyncio.to_thread(mirrors.store, folder, raw)
    try:
        beatmap = await asyncio.to_thread(parse, raw)
    except OsuFileError:
        beatmap = None
    if beatmap is None or beatmap.mode != 0 or len(beatmap.objects) < 2:
        async with factory() as session:
            await catalog.revision(session, entry["beatmap_id"], digest, beatmapset_id=entry["beatmapset_id"],
                                   ranked_status=entry.get("status"), mapper=entry.get("mapper"), source=source, now=now)
            await catalog.admit(session, entry["beatmap_id"], digest, False, "parse_failed", now)
            await session.commit()
        return "parse_failed"
    analyses = {}
    for mods in ANALYSED:
        features = await asyncio.to_thread(analyse, beatmap, mods)
        analyses[mods] = (features, await _stars(osu, entry["beatmap_id"], mods, entry["stars"]))
    prediction = None
    if model is not None:
        hr_cs = apply_mods(beatmap, "HR")[0]
        prediction = model.predict(analyses["NF"][0], beatmap.ar, hr_cs, {mods: analyses[mods][0] for mods in VIEWED if mods in analyses})
    async with factory() as session:
        made, _ = await catalog.revision(session, entry["beatmap_id"], digest, beatmapset_id=entry["beatmapset_id"],
                                         ranked_status=entry.get("status"), mapper=entry.get("mapper"), source=source,
                                         object_count=len(beatmap.objects), format_version=beatmap.format_version, now=now)
        made.max_combo = entry.get("max_combo")
        made.title = (entry.get("title") or "")[:200] or made.title
        made.artist = (entry.get("artist") or "")[:200] or made.artist
        made.version = (entry.get("version") or "")[:200] or made.version
        made.ranked_status = entry.get("status")
        made.beatmapset_id = made.beatmapset_id or entry["beatmapset_id"]
        made.mapper = made.mapper or entry.get("mapper")
        held = await _analysed(session, digest)
        for mods, (features, stars) in analyses.items():
            if mods in held:
                await _restar(session, digest, mods, stars)
                continue
            session.add(DuelMapAnalysis(revision_hash=digest, mods=mods, engine_version=ENGINE_VERSION,
                                        feature_version=FEATURE_VERSION, model_version=model.version if model else "none",
                                        stars=stars, features=features,
                                        predictions=prediction.probabilities if prediction and mods == "NF" else None,
                                        created_at=now))
        await catalog.admit(session, entry["beatmap_id"], digest, True, None, now)
        outcome = "unassigned"
        if prediction is not None and prediction.slot is not None:
            try:
                proposed = await catalog.propose(session, entry["beatmap_id"], prediction.slot, prediction.probability,
                                                 prediction.margin, model.version, now)
                outcome = f"proposed {proposed['slot']}" if proposed["slot"] else "unassigned"
            except catalog.CatalogRefusal as refusal:
                outcome = refusal.code.lower()
        await session.commit()
    return outcome

async def _restar(session, digest: str, mods: str, stars: Optional[float]) -> None:
    if stars is None:
        return
    row = await session.scalar(select(DuelMapAnalysis).where(DuelMapAnalysis.revision_hash == digest, DuelMapAnalysis.mods == mods,
                                                             DuelMapAnalysis.feature_version == FEATURE_VERSION))
    if row is not None and row.stars is None:
        row.stars = stars

async def discover(factory, fetch, osu, folder: str, least: float, most: float, pages: int, amount: int = 50,
                   model=None, pause: float = 1.0, report=print) -> dict[str, int]:
    counted: dict[str, int] = {}
    seen: set[int] = set()
    for page in range(pages):
        found = await mirrors.search(fetch, "ranked", page * amount, amount, least, most)
        if found is None:
            report("every mirror refused the search")
            break
        name, entries, total = found
        for entry in entries:
            if entry["beatmap_id"] in seen or not suitable(entry):
                continue
            seen.add(entry["beatmap_id"])
            outcome = await ingest(factory, fetch, osu, entry, folder, model)
            counted[outcome] = counted.get(outcome, 0) + 1
            report(f"{entry['beatmap_id']} {entry['artist']} - {entry['title']} [{entry['version']}] {entry['stars']:.2f}: {outcome}")
            if outcome != "known":
                await asyncio.sleep(pause)
        if total < amount:
            break
    return counted

async def _name_revision(session, revision: DuelMapRevision, folder: str) -> None:
    path = os.path.join(folder, f"{revision.hash}.osu")
    if not os.path.exists(path):
        return
    try:
        beatmap = await asyncio.to_thread(parse, await asyncio.to_thread(_read, path))
    except (OSError, OsuFileError):
        return
    revision.title, revision.artist, revision.version = beatmap.title[:200] or None, beatmap.artist[:200] or None, beatmap.version[:200] or None
    revision.mapper = revision.mapper or beatmap.creator[:64] or None
    await session.commit()

def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()

async def _example_file(factory, fetch, example: corpus.Example, folder: str, now: datetime) -> Optional[str]:
    async with factory() as session:
        held = await session.scalar(select(DuelMapRevision).where(DuelMapRevision.beatmap_id == example.beatmap_id)
                                    .order_by(DuelMapRevision.downloaded_at.desc()))
        if held is not None and await _analysed(session, held.hash) >= set(ANALYSED):
            if held.title is None:
                await _name_revision(session, held, folder)
            return held.hash
    fetched = await mirrors.fetch_osu(fetch, example.beatmapset_id or 0, example.beatmap_id, None)
    if fetched is None:
        return None
    source, raw = fetched
    digest = await asyncio.to_thread(mirrors.store, folder, raw)
    try:
        beatmap = await asyncio.to_thread(parse, raw)
    except OsuFileError:
        return None
    if beatmap.mode != 0 or len(beatmap.objects) < 2:
        return None
    analyses = {mods: await asyncio.to_thread(analyse, beatmap, mods) for mods in ANALYSED}
    async with factory() as session:
        revision = await session.scalar(select(DuelMapRevision).where(DuelMapRevision.beatmap_id == example.beatmap_id,
                                                                     DuelMapRevision.hash == digest))
        if revision is None:
            session.add(DuelMapRevision(beatmap_id=example.beatmap_id, beatmapset_id=example.beatmapset_id, hash=digest,
                                        format_version=beatmap.format_version, status="ok", source=source,
                                        object_count=len(beatmap.objects), downloaded_at=now, mapper=beatmap.creator[:64] or None,
                                        title=beatmap.title[:200] or None, artist=beatmap.artist[:200] or None, version=beatmap.version[:200] or None))
        held = await _analysed(session, digest)
        for mods, features in analyses.items():
            if mods not in held:
                session.add(DuelMapAnalysis(revision_hash=digest, mods=mods, engine_version=ENGINE_VERSION,
                                            feature_version=FEATURE_VERSION, model_version="none", stars=None,
                                            features=features, created_at=now))
        await session.commit()
    return digest

async def load_corpus(factory, fetch, folder: str, years=YEARS, pause: float = 1.0, report=print,
                      now: Optional[datetime] = None) -> dict[str, int]:
    now = now or utcnow()
    examples: list[corpus.Example] = []
    for year in years:
        body = await fetch(WIKI.format(year=year))
        found = corpus.read_wiki(body.decode("utf-8", errors="replace"), year, WIKI.format(year=year)) if body else []
        report(f"OWC {year}: {len(found)} maps")
        examples.extend(found)
    clashes = corpus.conflicts(examples)
    counted: dict[str, int] = {}
    for example in corpus.distinct(examples):
        digest = await _example_file(factory, fetch, example, folder, now)
        async with factory() as session:
            row = await session.scalar(select(DuelTrainingExample).where(
                DuelTrainingExample.corpus_version == corpus.CORPUS_VERSION, DuelTrainingExample.year == example.year,
                DuelTrainingExample.stage == example.stage, DuelTrainingExample.original_slot == example.original_slot,
                DuelTrainingExample.beatmap_id == example.beatmap_id))
            if row is None:
                row = DuelTrainingExample(corpus_version=corpus.CORPUS_VERSION, source=example.source, year=example.year,
                                          stage=example.stage, original_slot=example.original_slot, beatmap_id=example.beatmap_id,
                                          beatmapset_id=example.beatmapset_id, fetched_at=now)
                session.add(row)
            row.mods = GROUP_MODS.get(example.original_slot[:2])
            row.revision_hash = digest
            row.normalized_label = corpus.label(example.original_slot)
            row.conflict = example.beatmap_id in clashes
            await session.commit()
        outcome = "filed" if digest else "no_file"
        counted[outcome] = counted.get(outcome, 0) + 1
        report(f"{example.year} {example.stage} {example.original_slot} {example.beatmap_id}: {outcome}")
        if pause:
            await asyncio.sleep(pause)
    return counted

def resolved(example, labels: dict[tuple, str]) -> tuple[Optional[str], bool]:
    cell = (example.corpus_version, example.year, example.stage, example.original_slot)
    if cell + (int(example.beatmap_id),) in labels:
        chosen, verified = labels[cell + (int(example.beatmap_id),)], True
    elif cell + (None,) in labels:
        chosen, verified = labels[cell + (None,)], True
    else:
        chosen, verified = corpus.label(example.original_slot) or example.normalized_label, False
    chosen = corpus.read_back(chosen, example.original_slot)
    return (None if chosen == corpus.EXCLUDED else chosen), verified

async def load_missing(factory, fetch, folder: str, pause: float = 1.0, report=print, now: Optional[datetime] = None) -> dict[str, int]:
    now = now or utcnow()
    async with factory() as session:
        waiting = (await session.scalars(select(DuelTrainingExample).where(DuelTrainingExample.revision_hash.is_(None)))).all()
    counted: dict[str, int] = {}
    for row in waiting:
        example = corpus.Example(row.year, row.stage, row.original_slot, int(row.beatmap_id),
                                 int(row.beatmapset_id) if row.beatmapset_id else None, row.source)
        digest = await _example_file(factory, fetch, example, folder, now)
        if digest:
            async with factory() as session:
                held = await session.get(DuelTrainingExample, row.id)
                held.revision_hash = digest
                await session.commit()
        outcome = "filed" if digest else "no_file"
        counted[outcome] = counted.get(outcome, 0) + 1
        report(f"{row.corpus_version} {row.original_slot} {row.beatmap_id}: {outcome}")
        if pause:
            await asyncio.sleep(pause)
    return counted

async def corpus_labels(session) -> dict[tuple, str]:
    rows = (await session.scalars(select(DuelCorpusLabel).order_by(DuelCorpusLabel.updated_at))).all()
    return {(row.corpus_version, row.year, row.stage, row.original_slot, int(row.beatmap_id) if row.beatmap_id is not None else None): row.label
            for row in rows}

async def corpus_rows(factory) -> list[tuple[list[float], str, int, int, bool, bool]]:

    async with factory() as session:
        labels = await corpus_labels(session)
        examples = (await session.scalars(select(DuelTrainingExample).where(
            DuelTrainingExample.corpus_version.in_(corpus.LEARNED), DuelTrainingExample.conflict.is_(False),
            DuelTrainingExample.revision_hash.is_not(None)))).all()
        found = []
        for example in examples:
            label, verified = resolved(example, labels)
            if label is None:
                continue
            label = corpus.learned(label)
            plain, views = await seen_of(session, example.revision_hash)
            if not plain:
                continue
            found.append((seen_vector(plain, views), label, int(example.beatmapset_id or example.beatmap_id), example.year,
                          example.corpus_version == corpus.OWN_VERSION, verified))
    return found

def guesses(model: SlotModel, rows) -> tuple[list[str], list[str], list[bool]]:
    truth, guessed, confident = [], [], []
    for row, label, *_ in rows:
        normalised = model.normaliser.apply(row)
        probabilities = _softmax([b + sum(w * x for w, x in zip(ws, normalised)) for ws, b in zip(model.weights, model.bias)])
        ranked = sorted(range(len(probabilities)), key=lambda k: -probabilities[k])
        best = model.classes[ranked[0]]
        second = next((k for k in ranked[1:] if model.classes[k] != OTHER), None)
        margin = probabilities[ranked[0]] - (probabilities[second] if second is not None else 0.0)
        truth.append(label)
        guessed.append(best)
        if best != OTHER and catalog.confidence_level(probabilities[ranked[0]], margin) == "confident":
            confident.append(best == label)
    return truth, guessed, confident

def judged(truth: list[str], guessed: list[str], confident: list[bool]) -> dict[str, Any]:
    found = scored(truth, guessed)
    found["confident"] = {"count": len(confident), "precision": sum(confident) / len(confident) if confident else 0.0}
    return found

async def reassess(factory, model: SlotModel, now: Optional[datetime] = None) -> dict[str, int]:
    now = now or utcnow()
    counted: dict[str, int] = {}
    async with factory() as session:
        waiting = (await session.scalars(select(DuelMapAssignment).where(DuelMapAssignment.state.in_([catalog.UNASSIGNED, catalog.AUTO_ASSIGNED]),
                                                                         DuelMapAssignment.admitted.is_(True)))).all()
        for assignment in waiting:
            if assignment.state == catalog.AUTO_ASSIGNED and catalog.confidence_level(assignment.confidence, assignment.margin) == "confident":
                continue
            plain = await session.scalar(select(DuelMapAnalysis).where(DuelMapAnalysis.revision_hash == assignment.revision_hash,
                                                                      DuelMapAnalysis.mods == "NF",
                                                                      DuelMapAnalysis.feature_version == FEATURE_VERSION))
            rock = await session.scalar(select(DuelMapAnalysis).where(DuelMapAnalysis.revision_hash == assignment.revision_hash,
                                                                     DuelMapAnalysis.mods == "NFHR",
                                                                     DuelMapAnalysis.feature_version == FEATURE_VERSION))
            if plain is None or rock is None:
                counted["no_analysis"] = counted.get("no_analysis", 0) + 1
                continue
            _, views = await seen_of(session, assignment.revision_hash)
            prediction = model.predict(plain.features, plain.features["effective"]["ar"], rock.features["effective"]["cs"], views)
            outcome = "below_threshold"
            if prediction.slot is not None:
                try:
                    proposed = await catalog.propose(session, int(assignment.beatmap_id), prediction.slot, prediction.probability,
                                                     prediction.margin, model.version, now, unsure_again=True)
                    outcome = f"proposed {proposed['slot']}" if proposed["slot"] else "below_threshold"
                except catalog.CatalogRefusal as refusal:
                    outcome = refusal.code.lower()
            counted[outcome] = counted.get(outcome, 0) + 1
        await session.commit()
    return counted
