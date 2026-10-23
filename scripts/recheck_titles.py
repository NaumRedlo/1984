import argparse
import asyncio

from sqlalchemy import select

import db.models
from db.database import AsyncSessionFactory
from db.models.player import Player
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from utils.title_history import forget_history
from utils.title_progress import _CALCULATORS
from utils.titles import TITLE_REGISTRY

async def recheck(apply: bool, since: str) -> None:
    async with AsyncSessionFactory() as session:
        rows = (await session.execute(
            select(UserTitleProgress, Player).join(Player, Player.id == UserTitleProgress.player_id)
            .where(UserTitleProgress.unlocked.is_(True), UserTitleProgress.unlocked_at >= since)
            .order_by(UserTitleProgress.player_id, UserTitleProgress.title_code)
        )).all()
        lost = []
        for progress, player in rows:
            title = TITLE_REGISTRY.get(progress.title_code)
            calc = _CALCULATORS.get(progress.title_code)
            user = await session.scalar(select(User).where(User.player_id == player.id).limit(1))
            if title is None or calc is None or user is None:
                continue
            forget_history(session, player.id)
            raw = calc(user, player.id, session)
            value = await raw if hasattr(raw, "__await__") else raw
            if value < title.target:
                lost.append((progress, player, value, title.target))
                print(f"{player.osu_username} (player {player.id}): {progress.title_code} unlocked {progress.unlocked_at} now {value}/{title.target}")
        if not lost:
            print("every title since", since, "still holds")
            return
        if not apply:
            print(f"{len(lost)} titles would be taken back; run again with --apply to do it")
            return
        for progress, player, value, _ in lost:
            progress.unlocked = False
            progress.unlocked_at = None
            progress.current_value = value
            if player.active_title_code == progress.title_code:
                player.active_title_code = None
            for user in (await session.scalars(select(User).where(User.player_id == player.id))).all():
                if user.active_title_code == progress.title_code:
                    user.active_title_code = None
        await session.commit()
        print(f"{len(lost)} titles taken back")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-10-22")
    parser.add_argument("--apply", action="store_true")
    said = parser.parse_args()
    asyncio.run(recheck(said.apply, said.since))
