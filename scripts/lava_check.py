import asyncio
import getpass
import json
import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from services.lava_top import LavaClient, LavaConfig, LavaError, catalogue_prices


async def inspect_account(api_key: str) -> dict:
    config = LavaConfig(True, api_key)
    async with aiohttp.ClientSession() as session:
        client = LavaClient(session, config)
        products = await client.products()
        subscriptions = await client.subscriptions()
    counts = Counter(row.get("subscriptionStatus") or "UNKNOWN" for row in subscriptions)
    return {
        "plans": [asdict(price) for price in catalogue_prices(products)],
        "subscriptions_visible_to_api_key": len(subscriptions),
        "subscription_statuses": dict(counts),
    }


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    try:
        key = os.getenv("LAVA_TOP_API_KEY") or getpass.getpass("lava.top API key (hidden): ")
        result = asyncio.run(inspect_account(key))
    except (LavaError, ValueError) as exc:
        status = getattr(exc, "status", None)
        print(f"lava.top connection check failed{f' (HTTP {status})' if status else ''}. Check the key and provider availability.")
        return 1
    except (KeyboardInterrupt, EOFError):
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
