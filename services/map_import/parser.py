"""Parse user-supplied import targets — plain IDs, osu.ppy.sh URLs, or
public file-hosting links (Google Drive / MediaFire / direct .zip/.osz
URLs) — into a structured ImportTarget. Pure: no IO, no API calls.

Accepted forms (in order of detection):
    osu! beatmap/beatmapset URLs (forwarded to the osu! API resolver):
        "https://osu.ppy.sh/b/123"
        "https://osu.ppy.sh/beatmaps/123"
        "https://osu.ppy.sh/beatmapsets/456"
        "https://osu.ppy.sh/beatmapsets/456#osu/789"
        "https://osu.ppy.sh/beatmapsets/456/789"

    Public archive URLs (forwarded to the URL → file download path):
        "https://download####.mediafire.com/<hash>/<file>.zip"     direct
        "https://www.mediafire.com/file/<id>/<name>"                page  → scrape
        "https://drive.google.com/file/d/<id>/view"                 page  → rewrite
        "https://drive.google.com/uc?id=<id>&export=download"       direct
        "https://docs.google.com/uc?id=<id>"                        direct
        "https://example.com/foo/bar.zip"                           any direct link

    Bare digits → BEATMAP (heuristic; resolver falls back to set lookup).

Mega (mega.nz) is explicitly rejected: their AES-CTR encrypted streams
require the SDK and are not supported.

Anything else returns TargetKind.UNKNOWN with the raw text so callers
can report it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from urllib.parse import parse_qs, urlparse


class TargetKind(str, Enum):
    BEATMAP    = "beatmap"
    BEATMAPSET = "beatmapset"
    FILE_URL   = "file_url"   # Public direct/indirect link to a .zip/.osz archive
    UNSUPPORTED = "unsupported"  # Recognised host but cannot be downloaded (e.g. mega)
    UNKNOWN    = "unknown"


@dataclass(frozen=True)
class ImportTarget:
    kind:  TargetKind
    id:    Optional[int]   # beatmap_id / beatmapset_id when applicable
    raw:   str             # original text — for error reporting
    # FILE_URL only — the URL the downloader should hit. Equal to `raw` for
    # already-direct links; rewritten to the direct form for Google Drive.
    download_url: Optional[str] = field(default=None)
    # FILE_URL only — when set, downloader must scrape this hostname's
    # download page to extract the real binary URL. Currently 'mediafire'.
    scrape: Optional[str] = field(default=None)
    # UNSUPPORTED only — human-readable reason ("Mega не поддерживается").
    reason: Optional[str] = field(default=None)


# ── osu! URL patterns (tried first, before the file-host detector) ────────
_OSU_PATTERNS: list[tuple[re.Pattern, TargetKind]] = [
    # beatmapsets/<id>#osu/<diff_id>      — specific diff in a set
    (re.compile(r"osu\.ppy\.sh/beatmapsets/\d+(?:[#/])(?:osu/)?(\d+)"),
     TargetKind.BEATMAP),

    # beatmapsets/<id>                    — whole set
    (re.compile(r"osu\.ppy\.sh/beatmapsets/(\d+)"),
     TargetKind.BEATMAPSET),

    # beatmaps/<id>                        — single beatmap (new layout)
    (re.compile(r"osu\.ppy\.sh/beatmaps/(\d+)"),
     TargetKind.BEATMAP),

    # /b/<id>                              — single beatmap (short form)
    (re.compile(r"osu\.ppy\.sh/b/(\d+)"),
     TargetKind.BEATMAP),

    # /s/<id>                              — beatmapset (legacy short form)
    (re.compile(r"osu\.ppy\.sh/s/(\d+)"),
     TargetKind.BEATMAPSET),
]


# Archive extensions we'll accept on a direct link.
_ARCHIVE_EXTS = (".zip", ".osz", ".7z", ".rar")


def _classify_file_url(text: str) -> Optional[ImportTarget]:
    """Classify a non-osu URL. Returns a FILE_URL/UNSUPPORTED target or None
    if `text` isn't a URL at all (caller falls through to bare-id heuristic).
    """
    if not re.match(r"https?://", text, re.IGNORECASE):
        return None

    try:
        parsed = urlparse(text)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    path = parsed.path or ""

    # ── Mega — encrypted, no plain HTTP fetch ──────────────────────────
    if host.endswith("mega.nz") or host.endswith("mega.io") or host == "mega.co.nz":
        return ImportTarget(
            TargetKind.UNSUPPORTED, None, text,
            reason=(
                "Mega не поддерживается — файлы там зашифрованы. "
                "Скачайте архив локально и прикрепите файл к сообщению."
            ),
        )

    # ── Google Drive — rewrite shareable link to the direct uc? form ───
    if host.endswith("drive.google.com") or host.endswith("docs.google.com"):
        # Shareable: drive.google.com/file/d/<id>/view
        m = re.match(r"/file/d/([^/]+)", path)
        if m:
            file_id = m.group(1)
            direct = (
                f"https://drive.google.com/uc?export=download&id={file_id}"
            )
            return ImportTarget(
                TargetKind.FILE_URL, None, text, download_url=direct,
            )
        # Already-direct form: drive.google.com/uc?id=<id>&export=download
        qs = parse_qs(parsed.query or "")
        if "id" in qs:
            file_id = qs["id"][0]
            direct = (
                f"https://drive.google.com/uc?export=download&id={file_id}"
            )
            return ImportTarget(
                TargetKind.FILE_URL, None, text, download_url=direct,
            )
        # Folder/anonymized share — unsupported, ask to give a file link.
        return ImportTarget(
            TargetKind.UNSUPPORTED, None, text,
            reason=(
                "Google Drive: дайте ссылку на конкретный файл "
                "(`drive.google.com/file/d/.../view` или "
                "`drive.google.com/uc?id=...`)."
            ),
        )

    # ── MediaFire — direct host already returns the binary ─────────────
    # download####.mediafire.com/<hash>/<file>
    if re.match(r"^download\d*\.mediafire\.com$", host):
        return ImportTarget(
            TargetKind.FILE_URL, None, text, download_url=text,
        )
    # File page: www.mediafire.com/file/<id>/<name> — must scrape.
    if host.endswith("mediafire.com"):
        if "/file/" in path or "/file_premium/" in path:
            return ImportTarget(
                TargetKind.FILE_URL, None, text,
                download_url=text, scrape="mediafire",
            )
        return ImportTarget(
            TargetKind.UNSUPPORTED, None, text,
            reason=(
                "MediaFire: дайте ссылку вида "
                "`mediafire.com/file/.../...` или прямую "
                "`download####.mediafire.com/.../...zip`."
            ),
        )

    # ── Any other HTTP URL ending in a known archive extension ────────
    lower_path = path.lower().split("?", 1)[0]
    if lower_path.endswith(_ARCHIVE_EXTS):
        return ImportTarget(
            TargetKind.FILE_URL, None, text, download_url=text,
        )

    # Recognised as URL but not actionable — bubble up as UNKNOWN so the
    # caller's general "unrecognised" message fires.
    return None


def parse_import_target(text: str) -> ImportTarget:
    """Classify a single token. Whitespace-stripped input expected."""
    t = (text or "").strip()
    if not t:
        return ImportTarget(TargetKind.UNKNOWN, None, text)

    # osu! URLs first — they're the most common case and unambiguous.
    for pattern, kind in _OSU_PATTERNS:
        m = pattern.search(t)
        if m:
            try:
                return ImportTarget(kind, int(m.group(1)), text)
            except ValueError:
                continue

    # File-hosting / direct archive URLs.
    file_url = _classify_file_url(t)
    if file_url is not None:
        return file_url

    if t.isdigit():
        # Bare digits: default to BEATMAP. Beatmapset IDs are visually
        # indistinguishable from beatmap IDs, but in practice users supply
        # bare IDs from /b/ links. If we're wrong, the resolver gracefully
        # falls back to beatmapset lookup.
        return ImportTarget(TargetKind.BEATMAP, int(t), text)

    return ImportTarget(TargetKind.UNKNOWN, None, text)


def parse_many(text: str) -> list[ImportTarget]:
    """Split on whitespace/commas and classify each chunk."""
    parts = re.split(r"[\s,]+", (text or "").strip())
    return [parse_import_target(p) for p in parts if p]
