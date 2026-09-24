from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.models
from db.database import Base
from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from services.image.core import CardRenderer
from services.leaderboard.service import build_map_leaderboard

CHAT = -1001
MAP = 658127

def _picture(colour, size=(256, 256)) -> bytes:
    img = Image.new("RGB", size, colour)
    ImageDraw.Draw(img).ellipse((size[0] // 4, size[1] // 5, size[0] * 3 // 4, size[1] * 7 // 10),
                                fill=tuple(min(255, c + 90) for c in colour))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

class _Osu:
    def __init__(self, status: str):
        self.status = status

    async def get_beatmap(self, beatmap_id):
        return {
            "id": beatmap_id, "status": self.status, "version": "Fullerene's Extra",
            "difficulty_rating": 7.32, "bpm": 200.0, "total_length": 252,
            "beatmapset": {"id": 1, "artist": "xi", "title": "Blue Zenith",
                           "creator": "Fullerene", "user_id": 2, "covers": {}},
        }

PLAYERS = [
    ("NaumRedlo", "RU", 720.0, 99.2, 2100, "XH", "HD,HR"),
    ("nazeetskyyy", "US", 673.0, 98.6, 2010, "X", "DT"),
    ("rookie_main", "RU", 626.0, 98.0, 1920, "SH", "HD"),
    ("ppfarmer", "US", 579.0, 97.4, 1830, "S", ""),
    ("streamgod", "RU", 532.0, 96.8, 1740, "A", "FL"),
    ("acc_demon", "US", 485.0, 96.2, 1650, "A", "HD,DT"),
    ("tapper", "RU", 438.0, 95.6, 1560, "B", ""),
    ("fl_andy", "US", 391.0, 95.0, 1470, "C", "EZ"),
    ("miss_one", "RU", 344.0, 94.4, 1380, "D", "NF"),
]

async def build(status: str) -> dict:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    palette = [(60, 90, 160), (150, 70, 70), (70, 140, 90), (120, 90, 150), (160, 120, 60),
               (70, 130, 150), (140, 80, 120), (90, 110, 70), (110, 70, 90)]
    start = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    async with factory() as session:
        for i, (name, country, pp, acc, combo, rank, mods) in enumerate(PLAYERS):
            user = User(chat_id=CHAT, telegram_id=100 + i, osu_user_id=200 + i, osu_username=name,
                        country=country, avatar_data=_picture(palette[i]))
            session.add(user)
            await session.flush()
            loved = status == "loved"
            session.add(UserMapAttempt(
                user_id=user.id, score_id=1000 + i, beatmap_id=MAP,
                pp=0.0 if loved else pp, pp_estimated=pp if loved else None,
                score=int(pp * 14000), accuracy=acc, max_combo=combo, rank=rank, mods=mods,
                played_at=start + timedelta(days=9 - i),
            ))
        await session.commit()
        result = await build_map_leaderboard(session, _Osu(status), MAP, CHAT, sync=False)
    await engine.dispose()

    data = dict(result.data)
    data["viewer"] = next(r for r in result.rows if r["username"] == "tapper")
    title = data["map_title"]
    artist, _, name = title.partition(" - ")
    data["title"], data["artist"], data["version"] = name, artist, data["map_version"]
    data["footer"] = "lbm · the map's leaderboard in this chat"
    data["beatmap_cover_data"] = _picture((40, 60, 110), (1920, 1080))
    data["lang"] = "en"
    return data

async def main() -> None:
    renderer = CardRenderer()
    for status in ("ranked", "loved"):
        data = await build(status)
        buf = await renderer.generate_map_leaderboard_v2_async(data)
        out = Path(f"/tmp/lbm_demo_{status}.png")
        out.write_bytes(buf.getvalue())
        print(f"→ {out}")

if __name__ == "__main__":
    asyncio.run(main())
