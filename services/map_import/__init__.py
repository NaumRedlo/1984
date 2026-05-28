"""Cross-pool map ingest: parse user-provided links/IDs, drive both BSK and
HPS pool population.

Public surface:
    parse_import_target(text)  -> ImportTarget
    resolve_target(api, target) -> list[int]     beatmap_ids ready to ingest
    ingest_beatmap(api, bid, pools=(...)) -> dict per-pool status

Background discovery lives in `services.map_import.crawler`.
"""

from services.map_import.parser import (
    ImportTarget,
    TargetKind,
    parse_import_target,
)
from services.map_import.resolver import resolve_target
from services.map_import.ingest import (
    DEFAULT_POOLS,
    IngestReport,
    PoolName,
    PoolOutcome,
    ingest_beatmap,
    ingest_many,
)

__all__ = [
    "ImportTarget",
    "TargetKind",
    "parse_import_target",
    "resolve_target",
    "ingest_beatmap",
    "ingest_many",
    "IngestReport",
    "PoolOutcome",
    "PoolName",
    "DEFAULT_POOLS",
]
