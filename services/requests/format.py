"""Small shared formatting helpers for request displays."""

from __future__ import annotations

from utils.formatting.text import escape_html


def map_label(artist: str | None, title: str | None, version: str | None,
              beatmap_id: int | None = None) -> str:
    """One-line 'Artist - Title [Version]' label, resilient to missing parts."""
    head = f"{(artist or '').strip()} - {(title or '').strip()}".strip(" -")
    if not head:
        head = (title or "").strip() or (f"map {beatmap_id}" if beatmap_id else "map")
    v = (version or "").strip()
    return f"{head} [{v}]" if v else head


def map_url(beatmap_id: int | None, beatmapset_id: int | None = None) -> str | None:
    """Public osu! link to the difficulty (falls back to the plain beatmap URL)."""
    if beatmapset_id and beatmap_id:
        return f"https://osu.ppy.sh/beatmapsets/{beatmapset_id}#osu/{beatmap_id}"
    if beatmap_id:
        return f"https://osu.ppy.sh/beatmaps/{beatmap_id}"
    return None


def map_link_html(label: str, beatmap_id: int | None, beatmapset_id: int | None = None) -> str:
    """`label` as an HTML anchor to the map (escaped), or just the escaped label."""
    url = map_url(beatmap_id, beatmapset_id)
    safe = escape_html(label)
    return f'<a href="{url}">{safe}</a>' if url else safe


def stars_suffix(star_rating) -> str:
    """' · ★5.42' for a known star rating, else ''."""
    try:
        return f" · ★{float(star_rating):.2f}" if star_rating else ""
    except (TypeError, ValueError):
        return ""
