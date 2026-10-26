"""Build a diagnostic pool without creating a duel or contacting Bancho."""
import argparse
import asyncio
import json
import math
from collections import Counter

import db.models  # noqa: F401 — register tables before configuring relationships
from db.database import AsyncSessionFactory
from services.duels import generator
from services.duels.coordinator import catalogue
from services.duels.rules import SLOTS


def star_level(value):
    stars = float(value)
    if not math.isfinite(stars) or not 0 < stars <= 15:
        raise argparse.ArgumentTypeError("stars must be between 0 and 15")
    return stars


async def probe(first, second, human_only, seed):
    async with AsyncSessionFactory() as session:
        maps = await catalogue(session)
    if human_only:
        maps = [item for item in maps if item.level == "human"]
    def profile(stars):
        return {slot: generator.Skill(stars, 0.4) for slot in SLOTS}

    pool = generator.generate(maps, profile(first), profile(second), seed)
    return {"status": pool.status, "catalogue": dict(Counter(item.slot for item in maps)),
            "deficits": pool.deficits, "needs_consent": pool.needs_consent,
            "maps": [{"slot": entry.candidate.slot, "beatmap": entry.candidate.beatmap_id,
                      "stars": entry.candidate.stars, "review": entry.candidate.level} for entry in pool.entries]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=star_level, default=5.5, help="first synthetic player skill in stars")
    parser.add_argument("--second", type=star_level, default=5.5, help="second synthetic player skill in stars")
    parser.add_argument("--human-only", action="store_true")
    parser.add_argument("--seed", default="dossier-probe")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.first, args.second, args.human_only, args.seed)), ensure_ascii=False, indent=2))
