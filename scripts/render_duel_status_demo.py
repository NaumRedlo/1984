"""Render the active-duel status card to /tmp/ in several states.

Usage:
    PYTHONPATH=/home/naumredlo/1984 python3 scripts/render_duel_status_demo.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from services.image.core import CardRenderer


def _grad_cover(c_left, c_right):
    """A horizontal-gradient 'cover' so the centre blend is clearly visible."""
    w, h = 900, 250
    img = Image.new("RGB", (w, h))
    px = img.load()
    for x in range(w):
        t = x / (w - 1)
        col = tuple(int(c_left[i] + (c_right[i] - c_left[i]) * t) for i in range(3))
        for y in range(h):
            px[x, y] = col
    return img


def _avatar(rgb):
    return Image.new("RGB", (128, 128), rgb)


def main() -> None:
    r = CardRenderer()
    out = Path("/tmp")

    av1, av2 = _avatar((60, 90, 160)), _avatar((150, 70, 70))
    cov1 = _grad_cover((40, 60, 110), (70, 90, 150))     # P1 bluish
    cov2 = _grad_cover((130, 60, 60), (90, 40, 70))      # P2 reddish
    map_cov = _grad_cover((30, 45, 80), (60, 30, 70))

    p1 = {"username": "NaumRedlo", "country": "RU", "division": "Challenger II", "mu": 2050}
    p2 = {"username": "nazeetskyyy", "country": "RU", "division": "Challenger I", "mu": 2400}

    # ── Live ranked Bo10, mid-match ──────────────────────────────────────────
    rounds = [
        {"status": "completed", "winner": 1},
        {"status": "completed", "winner": 2},
        {"status": "void", "winner": None},
        {"status": "completed", "winner": 1},
        {"status": "completed", "winner": 1},
        {"status": "completed", "winner": 2},
        {"status": "playing", "winner": None},
    ]
    live = {
        "mode": "ranked", "status": "round_active",
        "total_rounds": 10, "win_target": 6, "current_round": 6,
        "p1": p1, "p2": p2, "score": (3, 2), "rounds": rounds,
        "current_map": {"title": "xi - Blue Zenith [Fullerene's Extra]",
                        "star_rating": 7.32, "beatmap_id": 658127,
                        "beatmapset_id": 292301},
    }
    buf = r.generate_duel_status_card(live, av1, av2, cov1, cov2, map_cov)
    (out / "duel_status_live.png").write_bytes(buf.getvalue())
    print("→ /tmp/duel_status_live.png")

    # ── Casual Bo5, just accepted (pool building) ────────────────────────────
    accepted = {
        "mode": "casual", "status": "accepted",
        "total_rounds": 5, "win_target": 3, "current_round": 0,
        "p1": p1, "p2": p2, "score": (0, 0), "rounds": [],
        "current_map": None,
    }
    buf = r.generate_duel_status_card(accepted, av1, av2, cov1, cov2, None)
    (out / "duel_status_accepted.png").write_bytes(buf.getvalue())
    print("→ /tmp/duel_status_accepted.png")

    # ── Long names → scaled, not truncated ───────────────────────────────────
    longn = {
        **live,
        "p1": {**p1, "username": "WubWoofWolfgang"},
        "p2": {**p2, "username": "ProfessorMaximilian"},
    }
    buf = r.generate_duel_status_card(longn, av1, av2, cov1, cov2, map_cov)
    (out / "duel_status_longnames.png").write_bytes(buf.getvalue())
    print("→ /tmp/duel_status_longnames.png")

    # ── No avatars / no cover (offline fallback look) ────────────────────────
    buf = r.generate_duel_status_card(live, None, None, None, None, None)
    (out / "duel_status_flat.png").write_bytes(buf.getvalue())
    print("→ /tmp/duel_status_flat.png")


if __name__ == "__main__":
    main()
