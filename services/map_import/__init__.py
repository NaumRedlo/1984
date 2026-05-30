"""Cross-pool map ingest: parse user-provided links/IDs, drive both BSK and
HPS pool population.

Public surface:
    parse_import_target(text)  -> ImportTarget
    resolve_target(api, target) -> list[int]     beatmap_ids ready to ingest
    ingest_beatmap(api, bid, pools=(...)) -> dict per-pool status
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
from services.map_import.file_url import (
    FileUrlResolveError,
    resolve_file_url,
    resolve_gofile,
    resolve_mediafire,
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
    "FileUrlResolveError",
    "resolve_file_url",
    "resolve_gofile",
    "resolve_mediafire",
]
