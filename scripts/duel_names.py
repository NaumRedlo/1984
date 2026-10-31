import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

import db.models
from config.settings import DUEL_OSU_FOLDER
from db.database import AsyncSessionFactory
from services.duels import importer, mirrors
from utils.osu.api_client import OsuApiClient

async def main() -> None:
    osu = OsuApiClient()
    try:
        timeout = aiohttp.ClientTimeout(total=60, sock_read=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            counted = await importer.rename(AsyncSessionFactory, mirrors.aiohttp_fetch(session), osu, DUEL_OSU_FOLDER)
    finally:
        await osu.close()
    print(counted)

if __name__ == "__main__":
    asyncio.run(main())
