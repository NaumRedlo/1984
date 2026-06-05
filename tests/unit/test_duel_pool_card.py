"""Duel pool-card render — the PLAYED overlay path.

Offline (covers=None): we only check that a `played` map renders without error,
produces a valid PNG, and changes the image versus the same map left available
(the dim wash + diagonal PLAYED stamp). Mirrors the lightweight render checks
used elsewhere — no network, no Telegram.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from services.image import card_renderer


def _maps(played_idx=None):
    base = [
        {"artist": "Camellia", "title": "Ghost", "version": "Spectre",
         "star_rating": 5.2, "length": 142, "bpm": 174, "cs": 4, "ar": 9.3,
         "od": 8.5, "hp_drain": 5, "beatmapset_id": 1},
        {"artist": "xi", "title": "Blue Zenith", "version": "FOUR DIMENSIONS",
         "star_rating": 7.1, "length": 150, "bpm": 200, "cs": 4, "ar": 9.8,
         "od": 9, "hp_drain": 6, "beatmapset_id": 2},
    ]
    for i, m in enumerate(base):
        m["status"] = "played" if i == played_idx else "available"
    return base


def _render(maps) -> bytes:
    data = {"mode": "ranked", "total_rounds": 10, "win_target": 6,
            "target_sr": 6.0, "maps": maps}
    return card_renderer.generate_duel_pool_card(data, covers=[None] * len(maps)).getvalue()


def test_pool_card_renders_valid_png():
    png = _render(_maps())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(BytesIO(png))
    assert img.width > 0 and img.height > 0


def test_played_overlay_changes_the_image():
    available = _render(_maps(played_idx=None))
    played = _render(_maps(played_idx=0))
    # Same canvas size, but the PLAYED card is dimmed + stamped → different bytes.
    assert Image.open(BytesIO(available)).size == Image.open(BytesIO(played)).size
    assert available != played


def test_played_status_is_robust_to_missing_fields():
    # A sparsely-populated row (no stats / no cover id) must still render the
    # stamp without raising.
    maps = [{"title": "x", "status": "played"},
            {"title": "y", "status": "available"}]
    png = _render(maps)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
