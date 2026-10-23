import asyncio
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from db.models.duel_catalog import DuelMapAnalysis, DuelMapRevision
from services.duels import catalog, mirrors
from services.duels.features import FEATURE_VERSION, analyse, apply_mods
from services.duels.generator import SLOT_MODS
from services.duels.osu_file import OsuFileError, parse
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

async def known(factory, entry: dict[str, Any]) -> bool:
    async with factory() as session:
        found = await session.scalar(select(DuelMapRevision.id).where(DuelMapRevision.beatmap_id == entry["beatmap_id"],
                                                                     DuelMapRevision.hash == (entry.get("checksum") or "")))
    return found is not None

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
        prediction = model.predict(analyses["NF"][0], beatmap.ar, hr_cs)
    async with factory() as session:
        made, _ = await catalog.revision(session, entry["beatmap_id"], digest, beatmapset_id=entry["beatmapset_id"],
                                         ranked_status=entry.get("status"), mapper=entry.get("mapper"), source=source,
                                         object_count=len(beatmap.objects), format_version=beatmap.format_version, now=now)
        made.max_combo = entry.get("max_combo")
        for mods, (features, stars) in analyses.items():
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
