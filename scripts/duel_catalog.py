import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

import db.models
from config.settings import DUEL_OSU_FOLDER
from db.database import AsyncSessionFactory
from services.duels import balance, importer, mirrors
from services.duels.slot_model import SlotModel
from utils.osu.api_client import OsuApiClient

async def main(least: float, most: float, pages: int, amount: int) -> None:
    published = await balance.published(AsyncSessionFactory)
    model = SlotModel.load(published.body) if published is not None else None
    print("slot model:", published.version if published is not None else "none, maps wait for a reviewer")
    osu = OsuApiClient()
    try:
        timeout = aiohttp.ClientTimeout(total=120, sock_read=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            counted = await importer.discover(AsyncSessionFactory, mirrors.aiohttp_fetch(session), osu, DUEL_OSU_FOLDER,
                                              least, most, pages, amount, model)
    finally:
        await osu.close()
    print(counted)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fill the duel catalogue with ranked osu! maps found on mirrors")
    parser.add_argument("--from", dest="least", type=float, default=4.0)
    parser.add_argument("--to", dest="most", type=float, default=8.0)
    parser.add_argument("--pages", type=int, default=4)
    parser.add_argument("--amount", type=int, default=50)
    said = parser.parse_args()
    asyncio.run(main(said.least, said.most, said.pages, said.amount))
