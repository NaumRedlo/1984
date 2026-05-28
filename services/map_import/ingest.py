"""Unified single-beatmap ingest: write the same map into BSK and/or HPS
pools by delegating to the existing per-pool ingest functions.

Public surface:
    PoolName = Literal["bsk", "hps"]
    IngestReport: dataclass with per-pool outcome
    ingest_beatmap(api, bid, pools=DEFAULT_POOLS) -> IngestReport
    ingest_many(api, ids, pools=DEFAULT_POOLS, *, concurrency=2) -> list[IngestReport]

We dispatch the per-pool ingests concurrently per beatmap. Across maps, an
asyncio.Semaphore bounds concurrency so we don't hammer the osu! API.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Iterable, Literal

from services.bsk.map_pool import add_map_to_pool as add_bsk
from services.hps.hps_pool import add_map_to_hps_pool as add_hps

logger = logging.getLogger(__name__)


PoolName = Literal["bsk", "hps"]
DEFAULT_POOLS: tuple[PoolName, ...] = ("bsk", "hps")


@dataclass
class PoolOutcome:
    pool:    PoolName
    status:  str            # "added" | "skipped" | "error"
    message: str = ""


@dataclass
class IngestReport:
    beatmap_id: int
    outcomes:   list[PoolOutcome] = field(default_factory=list)

    def summary(self) -> str:
        return ", ".join(f"{o.pool}={o.status}" for o in self.outcomes)

    @property
    def any_added(self) -> bool:
        return any(o.status == "added" for o in self.outcomes)


async def _run_one(pool: PoolName, api_client, beatmap_id: int) -> PoolOutcome:
    fn = {"bsk": add_bsk, "hps": add_hps}[pool]
    try:
        result = await fn(api_client, beatmap_id)
    except Exception as e:
        logger.warning(f"ingest_beatmap({pool}, {beatmap_id}) raised: {e}", exc_info=False)
        return PoolOutcome(pool, "error", str(e)[:200])

    if result is None:
        # `add_map_to_pool` returns None for both "already in pool" and
        # "API returned nothing". The disambiguating query lives in the
        # admin handler; here we keep the report tight.
        return PoolOutcome(pool, "skipped", "already-in-pool or api-empty")
    return PoolOutcome(pool, "added", "")


async def ingest_beatmap(
    api_client,
    beatmap_id: int,
    pools: Iterable[PoolName] = DEFAULT_POOLS,
) -> IngestReport:
    """Ingest one beatmap into the listed pools (default: both).

    Per-pool ingests run concurrently — the osu! API calls are independent
    and the DB sessions don't overlap.
    """
    pool_list = list(pools)
    if not pool_list:
        return IngestReport(beatmap_id=beatmap_id)

    outcomes = await asyncio.gather(
        *[_run_one(p, api_client, beatmap_id) for p in pool_list],
        return_exceptions=False,
    )
    return IngestReport(beatmap_id=beatmap_id, outcomes=list(outcomes))


async def ingest_many(
    api_client,
    beatmap_ids: Iterable[int],
    pools: Iterable[PoolName] = DEFAULT_POOLS,
    *,
    concurrency: int = 2,
) -> list[IngestReport]:
    """Ingest many beatmaps with bounded concurrency. Preserves input order
    in the returned list.

    `concurrency` bounds parallel maps — keep it low (2-3) so we stay polite
    to the osu! API rate limit (rate-limit logic already lives in the
    client, but giving it room helps avoid 429-driven backoffs).
    """
    sem = asyncio.Semaphore(max(1, concurrency))
    ids = list(beatmap_ids)

    async def _guarded(bid: int) -> IngestReport:
        async with sem:
            return await ingest_beatmap(api_client, bid, pools)

    return await asyncio.gather(*[_guarded(bid) for bid in ids])
