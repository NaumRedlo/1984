import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

import db.models
from config.settings import ADMIN_IDS, DUEL_OSU_FOLDER
from db.database import AsyncSessionFactory
from services.duels import balance, importer, mirrors, training

def _brief(found: dict) -> str:
    slots = {name: round(values["f1"], 2) for name, values in found["per_slot"].items() if values["support"]}
    return (f"{found['count']} maps, macro F1 {found['macro_f1']:.2f}, confident {found['confident']['count']} "
            f"with precision {found['confident']['precision']:.2f}, F1 by slot {json.dumps(slots)}")

async def load() -> None:
    timeout = aiohttp.ClientTimeout(total=120, sock_read=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        fetch = mirrors.aiohttp_fetch(session)
        print(await importer.load_corpus(AsyncSessionFactory, fetch, DUEL_OSU_FOLDER))
        print(await importer.load_missing(AsyncSessionFactory, fetch, DUEL_OSU_FOLDER))

async def train() -> None:
    made = await training.train(AsyncSessionFactory)
    if made is None:
        print("not enough labelled maps; run load first")
        return
    version, found = made
    print(version, "trained on", found["maps"], "maps:", found["corpus"], "from tournaments,", found["own"], "of them yours,",
          found["queue"], "queue decisions,", found["verified"], "tournament labels checked by people")
    for year, scored in found["years"].items():
        print(year, "held out:", _brief(scored))
    print("all years:", _brief(found["test"]))

async def publish(version: str, actor: int, reason: str) -> None:
    await balance.publish(AsyncSessionFactory, actor, version, reason, tuple(ADMIN_IDS))
    print("published", version)

async def assign() -> None:
    model = await balance.working_model(AsyncSessionFactory)
    if model is None:
        print("no published slot model that fits the analyser")
        return
    print(await importer.reassess(AsyncSessionFactory, model))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Teach the duel slot model on OWC 2021-2025 pools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("load", help="read OWC pools from the osu! wiki sources and fetch their .osu files from mirrors")
    commands.add_parser("train", help="train the next Kamerton on every labelled map and score it by holding out one year at a time")
    chosen = commands.add_parser("publish", help="make a shadow model the one that assigns slots")
    chosen.add_argument("version")
    chosen.add_argument("--actor", type=int, required=True, help="your player id in the bot database")
    chosen.add_argument("--reason", default="first model from OWC pools")
    commands.add_parser("assign", help="let the published model propose slots for catalogue maps that have none")
    said = parser.parse_args()
    if said.command == "load":
        asyncio.run(load())
    elif said.command == "train":
        asyncio.run(train())
    elif said.command == "publish":
        asyncio.run(publish(said.version, said.actor, said.reason))
    else:
        asyncio.run(assign())
