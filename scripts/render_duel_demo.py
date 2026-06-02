"""Render the DUEL cards exactly as they appear during a live duel between two
players, to /tmp/ for visual inspection.

During an active duel the round-by-round flow is delivered as *text*; the only
image cards a player ever sees are:

  * the TrueSkill rating card  (``/duelstats`` + the profile matchmaking panel)
  * the division-change card   (posted on finish when a division boundary is
    crossed)

This script simulates one ranked duel — two established players plus the
winner's promotion card — and one placement-phase player, so every state of
the card is exercised offline (no avatar / cover downloads).

Usage:
    PYTHONPATH=/home/naumredlo/1984 python3 scripts/render_duel_demo.py

Outputs:
    /tmp/duel_p1_ranked.png        — player 1 rating card (ranked, established)
    /tmp/duel_p2_ranked.png        — player 2 rating card (ranked, higher)
    /tmp/duel_placement.png        — a player still in placement (calibration)
    /tmp/duel_division_promote.png — winner's promotion card (finish)
    /tmp/duel_division_relegate.png— loser's relegation card (finish)
"""

from __future__ import annotations

from pathlib import Path

from services.image.core import CardRenderer
from utils.hp_calculator import get_division_for_conservative


def _cons(mu: float, sigma: float) -> float:
    return max(0.0, mu - 3.0 * sigma)


def main() -> None:
    outdir = Path("/tmp")
    r = CardRenderer()

    # ── Player 1 — established ranked duellist ───────────────────────────────
    p1_mu, p1_sigma = 2050.0, 158.0
    p1 = {
        "username": "NaumRedlo",
        "country": "RU",
        "mode": "ranked",
        "mu": p1_mu,
        "sigma": p1_sigma,
        "conservative": _cons(p1_mu, p1_sigma),
        "peak_mu": 2180.0,
        "wins": 41,
        "losses": 29,
        "duel_rank": 8,
        "duel_division": get_division_for_conservative(_cons(p1_mu, p1_sigma)),
        "placement_matches_left": 0,
    }

    # ── Player 2 — higher-rated opponent ─────────────────────────────────────
    p2_mu, p2_sigma = 2400.0, 142.0
    p2 = {
        "username": "nazeetskyyy",
        "country": "RU",
        "mode": "ranked",
        "mu": p2_mu,
        "sigma": p2_sigma,
        "conservative": _cons(p2_mu, p2_sigma),
        "peak_mu": 2495.0,
        "wins": 58,
        "losses": 33,
        "duel_rank": 4,
        "duel_division": get_division_for_conservative(_cons(p2_mu, p2_sigma)),
        "placement_matches_left": 0,
    }

    # ── A fresh player mid-placement (calibration block) ─────────────────────
    placement = {
        "username": "rookie_main",
        "country": "US",
        "mode": "ranked",
        "mu": 1600.0,
        "sigma": 333.0,
        "conservative": _cons(1600.0, 333.0),
        "peak_mu": 1600.0,
        "wins": 4,
        "losses": 2,
        "duel_rank": None,
        "duel_division": "",
        "placement_matches_left": 4,   # 6/10 played
    }

    for name, data in (("duel_p1_ranked", p1),
                       ("duel_p2_ranked", p2),
                       ("duel_placement", placement)):
        buf = r.generate_duel_card(data, avatar=None, cover=None)
        path = outdir / f"{name}.png"
        path.write_bytes(buf.getvalue())
        div = data["duel_division"] or "(placement)"
        print(f"[{name}] {data['username']} — {div} → {path}")

    # ── Finish: winner promoted, loser relegated across a boundary ───────────
    # P2 wins, climbs Challenger I → Virtuoso III.
    promote = {
        "username": "nazeetskyyy",
        "country": "RU",
        "mode": "ranked",
        "new_div": "Virtuoso III",
        "is_promotion": True,
        "duel_points": 2120.0,
        "occurred_at": "02.06.2026 01:24",
    }
    # P1 drops Challenger II → Challenger III.
    relegate = {
        "username": "NaumRedlo",
        "country": "RU",
        "mode": "ranked",
        "new_div": "Challenger III",
        "is_promotion": False,
        "duel_points": 1465.0,
        "occurred_at": "02.06.2026 01:24",
    }

    for name, data in (("duel_division_promote", promote),
                       ("duel_division_relegate", relegate)):
        buf = r.generate_duel_division_card(data, avatar=None, cover=None)
        path = outdir / f"{name}.png"
        path.write_bytes(buf.getvalue())
        arrow = "▲ promote" if data["is_promotion"] else "▼ relegate"
        print(f"[{name}] {data['username']} {arrow} {data['new_div']} → {path}")

    print("\nDone. Inspect with:\n  ls -la /tmp/duel_*.png")


if __name__ == "__main__":
    main()
