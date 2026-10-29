import asyncio
import random
from typing import Optional

from sqlalchemy import select

from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision, DuelTrainingExample
from services.duels import balance, catalog, generator, importer, mirrors
from services.duels.features import FEATURE_VERSION, analyse
from services.duels.generator import Candidate, SLOT_MODS
from services.duels.osu_file import OsuFileError, parse
from services.duels.rules import in_catalogue
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("duels.discovery")
SEARCH_SECONDS = 35
MAPS_MOST = 24
CACHE_MOST = 512

async def excluded(session) -> set[int]:
    assigned = await session.scalars(select(DuelMapAssignment.beatmap_id))
    taught = await session.scalars(select(DuelTrainingExample.beatmap_id))
    return set(assigned) | set(taught)


def predicted(model, revision, analyses) -> Optional[Candidate]:
    plain = analyses.get("NF")
    hr = analyses.get("NFHR")
    if plain is None or hr is None or revision.status != "ok" or revision.ranked_status != "ranked":
        return None
    prediction = model.predict(plain.features, plain.features["effective"]["ar"], hr.features["effective"]["cs"],
                               {mods: row.features for mods, row in analyses.items() if mods in importer.VIEWED})
    if prediction.slot not in SLOT_MODS or catalog.confidence_level(prediction.probability, prediction.margin) != "confident":
        return None
    measured = analyses.get("".join(SLOT_MODS[prediction.slot]))
    if measured is None or not in_catalogue(measured.stars):
        return None
    return Candidate(int(revision.beatmap_id), int(revision.beatmapset_id), revision.mapper or "", prediction.slot,
                     float(measured.stars), "confident", prediction.probability, revision.hash, source="model", model_version=model.version)


async def cached(factory, model, blocked: set[int]) -> list[Candidate]:
    async with factory() as session:
        revisions = (await session.scalars(select(DuelMapRevision).where(
            DuelMapRevision.source == "duel-discovery", DuelMapRevision.status == "ok")
            .order_by(DuelMapRevision.downloaded_at.desc()).limit(CACHE_MOST))).all()
        found = []
        for revision in revisions:
            if revision.beatmap_id in blocked:
                continue
            rows = (await session.scalars(select(DuelMapAnalysis).where(
                DuelMapAnalysis.revision_hash == revision.hash, DuelMapAnalysis.feature_version == FEATURE_VERSION))).all()
            candidate = predicted(model, revision, {row.mods: row for row in rows})
            if candidate is not None:
                found.append(candidate)
        return found


async def inspect(factory, fetch, osu, folder, entry, model) -> Optional[Candidate]:
    if not importer.suitable(entry):
        return None
    async with factory() as session:
        if entry["beatmap_id"] in await excluded(session):
            return None
        existing = await session.scalar(select(DuelMapRevision.id).where(DuelMapRevision.beatmap_id == entry["beatmap_id"]))
        if existing is not None:
            return None
    fetched = await mirrors.fetch_osu(fetch, entry["beatmapset_id"], entry["beatmap_id"], entry.get("checksum"))
    if fetched is None:
        return None
    _, raw = fetched
    try:
        beatmap = await asyncio.to_thread(parse, raw)
    except OsuFileError:
        return None
    if beatmap.mode != 0 or len(beatmap.objects) < 2:
        return None
    digest = await asyncio.to_thread(mirrors.store, folder, raw)
    revision = DuelMapRevision(beatmap_id=entry["beatmap_id"], beatmapset_id=entry["beatmapset_id"], hash=digest,
                               status="ok", ranked_status=entry["status"], source="duel-discovery", mapper=beatmap.creator,
                               title=beatmap.title[:200], artist=beatmap.artist[:200], version=beatmap.version[:200],
                               object_count=len(beatmap.objects), max_combo=entry.get("max_combo"),
                               format_version=beatmap.format_version, downloaded_at=utcnow())
    analyses = {}
    for mods in importer.ANALYSED:
        features = await asyncio.to_thread(analyse, beatmap, mods)
        stars = await importer._stars(osu, entry["beatmap_id"], mods, entry["stars"])
        analyses[mods] = DuelMapAnalysis(revision_hash=digest, mods=mods, engine_version=importer.ENGINE_VERSION,
                                       feature_version=FEATURE_VERSION, model_version=model.version, stars=stars, features=features)
    candidate = predicted(model, revision, analyses)
    async with factory() as session:
        if entry["beatmap_id"] in await excluded(session):
            return None
        session.add(revision)
        session.add_all(analyses.values())
        await session.commit()
    return candidate


async def candidates(factory, osu, aims: dict[str, float], seed: str) -> list[Candidate]:
    import aiohttp
    from config.settings import DUEL_OSU_FOLDER

    model = await balance.working_model(factory)
    if model is None:
        logger.warning("cannot generate unseen duel maps: no published compatible model")
        return []
    async with factory() as session:
        blocked = await excluded(session)
    found = await cached(factory, model, blocked)
    rng = random.Random(seed)
    ranges = []
    for slot, target in aims.items():
        factor = importer.DT_STARS if slot.startswith("DT") else 1.0
        band = (round(max(0, target - generator.WIDE_CORRIDOR) / factor, 2),
                round((target + generator.WIDE_CORRIDOR) / factor, 2))
        if band not in ranges:
            ranges.append(band)
    rng.shuffle(ranges)

    async def search():
        tried = {candidate.beatmap_id for candidate in found} | blocked
        inspected = 0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10, sock_read=5)) as session:
            fetch = mirrors.aiohttp_fetch(session)
            for least, most in ranges:
                response = await mirrors.search(fetch, "ranked", rng.randrange(4) * 50, 50, least, most)
                entries = list(response[1]) if response else []
                rng.shuffle(entries)
                for entry in entries:
                    if entry["beatmap_id"] in tried or not importer.suitable(entry):
                        continue
                    tried.add(entry["beatmap_id"])
                    inspected += 1
                    candidate = await inspect(factory, fetch, osu, DUEL_OSU_FOLDER, entry, model)
                    if candidate is not None:
                        found.append(candidate)
                    if inspected >= MAPS_MOST:
                        return
    try:
        await asyncio.wait_for(search(), SEARCH_SECONDS)
    except asyncio.TimeoutError:
        logger.info("duel map discovery reached its time budget; using %s analysed candidates", len(found))
    async with factory() as session:
        blocked = await excluded(session)
    return [candidate for candidate in found if candidate.beatmap_id not in blocked]
