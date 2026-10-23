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
from services.duels import balance, importer, mirrors
from services.duels.slot_model import SlotModel

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
    made = await importer.train_corpus(AsyncSessionFactory)
    if made is None:
        print("not enough labelled maps; run load first")
        return
    version, found = made
    print("shadow model", version, "trained on", found["train"], "maps,", found["own"], "of them yours,", found["verified"], "labels checked by people")
    print("2024 check:", _brief(found["tune"]))
    print("2025 test: ", _brief(found["test"]))

async def publish(version: str, actor: int, reason: str) -> None:
    await balance.publish(AsyncSessionFactory, actor, version, reason, tuple(ADMIN_IDS))
    print("published", version)

async def assign() -> None:
    published = await balance.published(AsyncSessionFactory)
    if published is None:
        print("no published slot model")
        return
    print(await importer.reassess(AsyncSessionFactory, SlotModel.load(published.body)))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Teach the duel slot model on OWC 2021-2025 pools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("load", help="read OWC pools from the osu! wiki sources and fetch their .osu files from mirrors")
    commands.add_parser("train", help="train a shadow model on 2021-2023 and score it on 2024 and 2025")
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
