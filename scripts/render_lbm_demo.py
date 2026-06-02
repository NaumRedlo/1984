"""Render the map-leaderboard (`lbm`) card to /tmp/ with synthetic data so the
podium-panel corners can be inspected without hitting the osu! API.

Usage:
    PYTHONPATH=/home/naumredlo/1984 python3 scripts/render_lbm_demo.py
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from services.image.leaderboard import LeaderboardCardGenerator


def _cover_bytes(rgb: tuple[int, int, int]) -> bytes:
    """A flat-colour 'cover' so the podium panel has a visible background."""
    img = Image.new("RGB", (360, 200), rgb)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _avatar_bytes(rgb: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (128, 128), rgb)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    palette = [
        (60, 90, 160), (150, 70, 70), (70, 140, 90), (120, 90, 150),
        (160, 120, 60), (70, 130, 150), (140, 80, 120), (90, 110, 70),
        (110, 70, 90),
    ]
    names = ["NaumRedlo", "nazeetskyyy", "rookie_main", "ppfarmer",
             "streamgod", "acc_demon", "tapper", "fl_andy", "miss_one"]
    grades = ["XH", "X", "SH", "S", "A", "A", "B", "C", "D"]
    mods = ["HD,HR", "DT", "HD", "", "FL", "HD,DT", "", "EZ", "NF"]

    rows = []
    for i in range(9):
        av = _avatar_bytes(palette[i])
        rows.append({
            "position": i + 1,
            "username": names[i],
            "country": "RU" if i % 2 == 0 else "US",
            "pp": 720 - i * 47,
            "accuracy": 99.2 - i * 0.6,
            "combo": 2100 - i * 90,
            "rank": grades[i],
            "mods": mods[i],
            "avatar_data": av,
            "cover_data": _cover_bytes(palette[i]),
            # The sync renderer reads extended-row avatars from `_avatar_img`
            # (the async wrapper normally pre-decodes it from avatar_data).
            "_avatar_img": Image.open(BytesIO(av)).convert("RGBA"),
        })

    data = {
        "map_title": "xi - Blue Zenith",
        "map_version": "Fullerene's Extra",
        "beatmap_id": 658127,
        "star_rating": 7.32,
        "bpm": 200.0,
        "total_length": 252,
        "total_plays": 1234,
        "unique_players": 312,
        "beatmap_status": 1,
        "mapper_name": "Fullerene",
        "rows": rows,
        "page": 0,
    }

    r = LeaderboardCardGenerator()
    buf = r.generate_map_leaderboard_card(data)
    out = Path("/tmp/lbm_demo.png")
    out.write_bytes(buf.getvalue())
    print(f"→ {out}")


if __name__ == "__main__":
    main()
