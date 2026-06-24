"""Render the TITLES COLLECTION card to /tmp/ for visual inspection.

Usage:
    PYTHONPATH=. python scripts/render_titles_demo.py

Synthetic data — no DB or network. Builds a fake progress list straight from
the registry, so it shows the full 7-tier rarity spread.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from services.image.core import card_renderer
from services.image.render.titles import build_titles_card_data
from utils.titles import TITLE_REGISTRY
from utils.title_progress import build_titles_summary


# Codes left LOCKED, to show a realistic mix (the rest unlock).
_LOCKED = {"ss_8star", "ss_hddt_75star", "fc_marathon_30m", "td_4star"}
# Code shown mid-progress (counts toward a large target).
_PARTIAL = "played_100k"


def _synthetic_progress():
    base = datetime(2026, 6, 1, 12, 0, 0)
    out = []
    for i, (code, td) in enumerate(TITLE_REGISTRY.items()):
        unlocked = code not in _LOCKED and code != _PARTIAL
        if code == _PARTIAL:
            current, target = int(td.target * 0.62), td.target
        else:
            current, target = (td.target if unlocked else 0), td.target
        out.append({
            "code": code,
            "name": td.name,
            "description": td.description,
            "flavor": td.flavor,
            "target": target,
            "current": current,
            "progress_pct": 62.0 if code == _PARTIAL else (100.0 if unlocked else 0.0),
            "unlocked": unlocked,
            "unlocked_at": (base + timedelta(days=i)) if unlocked else None,
            "color": td.color,
            "rarity": td.rarity,
            "rarity_label": td.rarity_label,
            "secret": td.secret,
            "is_active": False,
        })
    return out


def main():
    progress = _synthetic_progress()
    summary = build_titles_summary(progress)

    for page in range(summary_pages := ((len(progress) + 9) // 10)):
        data = build_titles_card_data(
            username="Stepa", handle="@stepaa", country="kz",
            progress_list=progress, summary=summary,
            filter="all", page=page, rarest_global_pct=0.7,
        )
        buf = card_renderer.generate_titles_card(data)
        path = f"/tmp/titles_dashboard_p{page + 1}.png"
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        print("wrote", path)

    # A filtered view (mythic only) to sanity-check the tab highlight + filter.
    data = build_titles_card_data(
        username="Stepa", handle="@stepaa", country="kz",
        progress_list=progress, summary=summary,
        filter="mythic", page=0, rarest_global_pct=0.7,
    )
    buf = card_renderer.generate_titles_card(data)
    with open("/tmp/titles_dashboard_mythic.png", "wb") as f:
        f.write(buf.getvalue())
    print("wrote /tmp/titles_dashboard_mythic.png")
    print(f"pages={summary_pages} unlocked={summary['unlocked']}/{summary['total']}")


if __name__ == "__main__":
    main()
