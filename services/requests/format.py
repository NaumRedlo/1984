"""Small shared formatting helpers for request displays."""

from __future__ import annotations


def map_label(artist: str | None, title: str | None, version: str | None,
              beatmap_id: int | None = None) -> str:
    """One-line 'Artist - Title [Version]' label, resilient to missing parts."""
    head = f"{(artist or '').strip()} - {(title or '').strip()}".strip(" -")
    if not head:
        head = (title or "").strip() or (f"map {beatmap_id}" if beatmap_id else "map")
    v = (version or "").strip()
    return f"{head} [{v}]" if v else head
