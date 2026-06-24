"""Render the profile dashboard card to /tmp/ for visual inspection.

Usage:
    PYTHONPATH=. python scripts/render_profile_demo.py

Uses synthetic data — no DB or network required (image downloads are best-effort
and simply skipped if offline, so panels render with placeholder slots).
"""

from __future__ import annotations

import asyncio

from services.image.core import card_renderer


def _synthetic() -> dict:
    import math

    # pp history (~90 days), wobbling upward to ~6421 like the mockup.
    pp_history = [
        5000 + i * 16 + int(280 * math.sin(i / 6.0)) + int(140 * math.sin(i / 2.3))
        for i in range(90)
    ]
    pp_history[-1] = 6421
    rank_history = [16000 - i * 8 + (i % 7) * 30 for i in range(90)]
    top_scores = [
        {
            "rank": rank,
            "artist": artist,
            "title": title,
            "version": "Insane",
            "pp": pp,
            "accuracy": acc,
            "max_combo": combo,
            "mods": mods,
            "beatmapset_id": bsid,
            "creator": "mapper",
        }
        for rank, artist, title, pp, acc, combo, mods, bsid in [
            ("S", "Camellia", "Ghost", 512, 98.73, 1820, "HD,DT", 1084984),
            ("S", "xi", "Blue Zenith", 498, 98.12, 2100, "HR", 292301),
            ("A", "Sota Fujimori", "polygon", 421, 96.71, 1540, "HD", 774965),
            ("S", "DragonForce", "Through the Fire", 567, 99.01, 1320, "", 41823),
            ("A", "Yooh", "Selene", 389, 95.28, 980, "FL,HD", 1234567),
        ]
    ]
    return {
        "username": "Stepa",
        "handle": "@stepaa",
        "osu_id": 12345678,
        "country": "kz",
        "country_name": "Kazakhstan",
        "pp": 6421,
        "global_rank": 15392,
        "country_rank": 124,
        "accuracy": 98.41,
        "play_count": 83492,
        "play_time": "1248h",
        "ranked_score": 184_223_991_002,
        "total_score": 98_765_432_109,
        "total_hits": 12_842_591,
        "maximum_combo": 2341,
        "replays_watched": 1248,
        "level": 97,
        "level_progress": 82,
        "grade_counts": {"ss": 88, "ssh": 38, "s": 200, "sh": 142, "a": 1248, "b": 1872, "c": 2341, "d": 1021},
        "total_maps": 6950,
        "is_online": True,
        "is_supporter": True,
        "title": "The Machine",
        "title_color": (229, 57, 53),
        "title_outline": True,
        "join_date": "2021-08-18T10:22:00+00:00",
        "last_visit": "2026-06-20T09:00:00+00:00",
        "avatar_url": "https://a.ppy.sh/2",
        "cover_url": "https://assets.ppy.sh/user-profile-covers/2/1.jpeg",
        "pp_history": pp_history,
        "rank_history": rank_history,
        "top_scores": top_scores,
    }


async def main() -> None:
    data = _synthetic()
    buf = await card_renderer.generate_profile_dashboard_async(data)
    out = "/tmp/profile_dashboard.png"
    with open(out, "wb") as f:
        f.write(buf.read())
    print(f"wrote {out}")

    # Empty/edge case — no scores, no history, missing fields.
    sparse = {"username": "NoData", "osu_id": 1, "country": "__", "rank_history": [], "top_scores": []}
    buf2 = await card_renderer.generate_profile_dashboard_async(sparse)
    with open("/tmp/profile_dashboard_empty.png", "wb") as f:
        f.write(buf2.read())
    print("wrote /tmp/profile_dashboard_empty.png")


if __name__ == "__main__":
    asyncio.run(main())
