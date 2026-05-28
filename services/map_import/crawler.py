"""Autonomous map crawler — walks the osu! beatmapset library and feeds
unseen maps into both pools.

One iteration:
  1. Read settings (enabled, sr-targets, per-cycle budget).
  2. For each SR target zone in rotation:
       a. Query osu! API `beatmapsets/search` with appropriate filters.
       b. Walk results page-by-page, keep diffs that:
            - mode_int == 0 (osu!standard)
            - 60s ≤ total_length ≤ 600s
            - SR within zone window
            - not already in BskMapPool nor HpsMapPool
       c. Stop when we collect `per_zone_budget` candidates.
  3. Ingest the collected ids via services.map_import.ingest.ingest_many.

The crawler is intentionally polite: small per-cycle budget (default 20),
bounded concurrency on ingest, a small sleep between API pages.
Settings live in BotSettings (key/value rows) so admins can tune without
redeploys.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from db.database import get_db_session
from db.models.bot_settings import BotSettings
from db.models.bsk_map_pool import BskMapPool
from db.models.hps_map_pool import HpsMapPool
from services.map_import.ingest import (
    DEFAULT_POOLS,
    IngestReport,
    ingest_many,
)

logger = logging.getLogger(__name__)


# ─── Settings ──────────────────────────────────────────────────────────────

# Default SR zones — three windows roughly mirroring tier boundaries with
# slight overlap to keep a healthy supply at each tier's edges.
_DEFAULT_ZONES: list[tuple[float, float]] = [
    (2.0, 4.5),
    (4.5, 7.0),
    (7.0, 10.0),
]

SETTING_ENABLED       = "map_crawler_enabled"
SETTING_BUDGET        = "map_crawler_budget"
SETTING_INTERVAL_H    = "map_crawler_interval_hours"
SETTING_LAST_RUN      = "map_crawler_last_run"
SETTING_LAST_REPORT   = "map_crawler_last_report"
SETTING_ZONES_JSON    = "map_crawler_zones"

DEFAULT_BUDGET    = 20
DEFAULT_INTERVAL  = 6        # hours between cycles
PAGE_DELAY        = 0.4      # sleep between API search pages
MAX_PAGES_PER_ZONE = 8       # cap pagination so a sparse zone can't loop


@dataclass
class CrawlerConfig:
    enabled:  bool
    budget:   int           # max ids ingested per cycle (across all zones)
    interval_hours: int
    zones:    list[tuple[float, float]]


async def _read_setting(key: str) -> Optional[str]:
    async with get_db_session() as session:
        row = (await session.execute(
            select(BotSettings).where(BotSettings.key == key)
        )).scalar_one_or_none()
        return row.value if row else None


async def _write_setting(key: str, value: str) -> None:
    """Upsert via sqlite-specific ON CONFLICT — same pattern as the other
    BotSettings writers in this repo."""
    async with get_db_session() as session:
        stmt = sqlite_insert(BotSettings).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value})
        await session.execute(stmt)
        await session.commit()


async def read_config() -> CrawlerConfig:
    enabled = (await _read_setting(SETTING_ENABLED)) == "1"
    budget_raw = await _read_setting(SETTING_BUDGET)
    interval_raw = await _read_setting(SETTING_INTERVAL_H)
    zones_raw = await _read_setting(SETTING_ZONES_JSON)

    budget = int(budget_raw) if (budget_raw or "").isdigit() else DEFAULT_BUDGET
    try:
        interval = int(interval_raw) if interval_raw else DEFAULT_INTERVAL
    except ValueError:
        interval = DEFAULT_INTERVAL
    interval = max(1, min(interval, 168))  # 1h..1w

    zones: list[tuple[float, float]] = []
    if zones_raw:
        try:
            zones = [tuple(z) for z in json.loads(zones_raw)]
        except Exception:
            zones = []
    if not zones:
        zones = list(_DEFAULT_ZONES)

    return CrawlerConfig(
        enabled=enabled, budget=budget, interval_hours=interval, zones=zones,
    )


async def write_last_run(report: dict) -> None:
    await _write_setting(SETTING_LAST_RUN,
                         datetime.now(timezone.utc).isoformat())
    await _write_setting(SETTING_LAST_REPORT, json.dumps(report))


# ─── Pool membership cache ─────────────────────────────────────────────────

async def _known_ids() -> set[int]:
    """Set of beatmap_ids present in either pool. Read once per cycle."""
    async with get_db_session() as session:
        bsk_ids = (await session.execute(
            select(BskMapPool.beatmap_id)
        )).scalars().all()
        hps_ids = (await session.execute(
            select(HpsMapPool.beatmap_id)
        )).scalars().all()
    return set(int(i) for i in bsk_ids) | set(int(i) for i in hps_ids)


# ─── One crawl cycle ───────────────────────────────────────────────────────

@dataclass
class CrawlReport:
    ran_at:        str
    zones:         list[tuple[float, float]]
    found_candidates: int
    ingested_ids:  list[int]
    added_per_pool: dict[str, int]
    skipped_per_pool: dict[str, int]
    errors_per_pool: dict[str, int]
    notes:         list[str]

    def to_dict(self) -> dict:
        return {
            "ran_at":           self.ran_at,
            "zones":            self.zones,
            "found_candidates": self.found_candidates,
            "ingested_ids":     self.ingested_ids,
            "added_per_pool":   self.added_per_pool,
            "skipped_per_pool": self.skipped_per_pool,
            "errors_per_pool":  self.errors_per_pool,
            "notes":            self.notes,
        }


async def _collect_candidates(
    api_client,
    zone: tuple[float, float],
    *,
    quota: int,
    known: set[int],
    seen_in_run: set[int],
) -> list[int]:
    """Walk beatmapsets/search until we have `quota` fresh candidates from `zone`."""
    lo, hi = zone
    out: list[int] = []
    cursor: Optional[str] = None
    pages = 0

    while len(out) < quota and pages < MAX_PAGES_PER_ZONE:
        # The osu! search endpoint doesn't filter by SR directly so we sort
        # by difficulty_rating ascending and slice by zone client-side. To
        # avoid burning pages on already-seen sets we mix in pseudo-random
        # sort tiebreakers via different sort orders per zone.
        sort = "difficulty_rating_asc" if random.random() < 0.7 else "plays_desc"
        try:
            page = await api_client.search_beatmapsets(
                status="ranked", mode="osu",
                sort=sort, cursor_string=cursor,
            )
        except Exception as e:
            logger.warning(f"crawler: search page failed (zone={zone}): {e}")
            break
        if not page:
            break

        bsets = page.get("beatmapsets") or []
        if not bsets:
            break

        for bset in bsets:
            for bm in bset.get("beatmaps", []):
                if int(bm.get("mode_int", 0)) != 0:
                    continue
                bid = bm.get("id")
                if not bid:
                    continue
                bid = int(bid)
                if bid in known or bid in seen_in_run:
                    continue
                sr = float(bm.get("difficulty_rating") or 0)
                if sr < lo or sr >= hi:
                    continue
                length = int(bm.get("total_length") or 0)
                if length < 60 or length > 600:
                    continue
                out.append(bid)
                seen_in_run.add(bid)
                if len(out) >= quota:
                    break
            if len(out) >= quota:
                break

        cursor = page.get("cursor_string")
        if not cursor:
            break
        pages += 1
        await asyncio.sleep(PAGE_DELAY)

    return out


async def run_one_cycle(
    api_client,
    *,
    config: Optional[CrawlerConfig] = None,
) -> CrawlReport:
    """Run one crawl cycle. Safe to call manually for tests / admin trigger."""
    cfg = config or await read_config()
    started = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []

    if not cfg.enabled and config is None:
        # Allow manual override via passing a config explicitly.
        notes.append("crawler disabled in settings — no-op")
        return CrawlReport(
            ran_at=started, zones=cfg.zones, found_candidates=0,
            ingested_ids=[], added_per_pool={}, skipped_per_pool={},
            errors_per_pool={}, notes=notes,
        )

    known = await _known_ids()
    notes.append(f"pool snapshot: {len(known)} known beatmap_ids")

    # Even per-zone budget. If `len(zones)` doesn't divide evenly the
    # remainder rotates randomly so each zone gets a fair share over time.
    per_zone = max(1, cfg.budget // max(1, len(cfg.zones)))
    leftover = cfg.budget - per_zone * len(cfg.zones)
    quotas = [per_zone] * len(cfg.zones)
    for i in random.sample(range(len(cfg.zones)), leftover):
        quotas[i] += 1

    seen_in_run: set[int] = set()
    candidates: list[int] = []
    for zone, q in zip(cfg.zones, quotas):
        zone_ids = await _collect_candidates(
            api_client, zone, quota=q, known=known, seen_in_run=seen_in_run,
        )
        notes.append(f"zone {zone[0]}-{zone[1]}: +{len(zone_ids)} candidates")
        candidates.extend(zone_ids)

    if not candidates:
        notes.append("no fresh candidates — pool is up to date for these zones")
        report = CrawlReport(
            ran_at=started, zones=cfg.zones, found_candidates=0,
            ingested_ids=[], added_per_pool={}, skipped_per_pool={},
            errors_per_pool={}, notes=notes,
        )
        await write_last_run(report.to_dict())
        return report

    reports: list[IngestReport] = await ingest_many(
        api_client, candidates, pools=DEFAULT_POOLS, concurrency=2,
    )

    added:   dict[str, int] = {}
    skipped: dict[str, int] = {}
    errored: dict[str, int] = {}
    ingested_ok: list[int] = []
    for r in reports:
        for o in r.outcomes:
            if o.status == "added":
                added[o.pool] = added.get(o.pool, 0) + 1
            elif o.status == "skipped":
                skipped[o.pool] = skipped.get(o.pool, 0) + 1
            else:
                errored[o.pool] = errored.get(o.pool, 0) + 1
        if r.any_added:
            ingested_ok.append(r.beatmap_id)

    notes.append(
        f"ingest: +{sum(added.values())} added, "
        f"~{sum(skipped.values())} skipped, "
        f"!{sum(errored.values())} errors"
    )
    report = CrawlReport(
        ran_at=started, zones=cfg.zones,
        found_candidates=len(candidates), ingested_ids=ingested_ok,
        added_per_pool=added, skipped_per_pool=skipped,
        errors_per_pool=errored, notes=notes,
    )
    await write_last_run(report.to_dict())
    return report
