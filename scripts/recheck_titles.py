from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select, update

from db.database import AsyncSessionFactory, engine
from db.models.player import Player
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from utils.title_progress import _CALCULATORS
from utils.titles import TITLE_REGISTRY

TIGHTENED = (
    "fc_len_5m", "fc_bpm_210", "session_30maps", "lowacc_streak_10", "ss_streak_10", "ss_bpm240", "session_3h", "off_day",
)
RE_READ = (
    "dejavu", "reeducated", "perfectionist", "magic7", "masks_5", "heavy_hand", "sr_10", "hdhr_fc7", "fc_bpm_250",
    "rank_d", "short_30", "td_4star", "fl_6star", "ss_7star", "ss_fl_55star", "ss_8star", "ss_hddt_75star",
    "fc_marathon_30m", "ss_hdfl_5", "ez_pass_7", "doublethink",
)
CODES = TIGHTENED + RE_READ

async def examine(session, codes):
    users = {}
    for user in (await session.execute(select(User).where(User.player_id.isnot(None)))).scalars().all():
        users.setdefault(user.player_id, user)
    names = {p.id: p.osu_username for p in (await session.execute(select(Player))).scalars().all()}
    rows = (await session.execute(
        select(UserTitleProgress).where(UserTitleProgress.title_code.in_(codes), UserTitleProgress.unlocked.is_(True))
    )).scalars().all()
    found = []
    for row in rows:
        user = users.get(row.player_id)
        calc = _CALCULATORS.get(row.title_code)
        if user is None or calc is None:
            continue
        raw = calc(user, row.player_id, session)
        current = await raw if hasattr(raw, "__await__") else raw
        target = TITLE_REGISTRY[row.title_code].target
        found.append((row, names.get(row.player_id, str(row.player_id)), int(current), target, current < target))
    return found

async def main(args) -> int:
    if args.migrate:
        from db.migrations import run_all_migrations
        await run_all_migrations(engine)
    codes = tuple(args.codes) or CODES
    unknown = [code for code in codes if code not in TITLE_REGISTRY]
    if unknown:
        print("unknown titles:", ", ".join(unknown))
        return 2
    async with AsyncSessionFactory() as session:
        found = await examine(session, codes)
        by_code = {}
        for row, name, current, target, lost in found:
            by_code.setdefault(row.title_code, []).append((name, current, target, lost))
        for code in codes:
            held = by_code.get(code, [])
            lost = [item for item in held if item[3]]
            print(f"{code:22s} holders {len(held):3d}  would lose {len(lost):3d}" + (f"  ({', '.join(f'{n} {c}/{t}' for n, c, t, _ in lost)})" if lost else ""))
        if not args.apply:
            print("dry run: nothing was changed; add --apply to withdraw the titles listed above")
            return 0
        withdrawn = 0
        for row, name, current, target, lost in found:
            if not lost:
                continue
            row.unlocked = False
            row.unlocked_at = None
            row.current_value = current
            await session.execute(
                update(Player).where(Player.id == row.player_id, Player.active_title_code == row.title_code).values(active_title_code=None)
            )
            await session.execute(
                update(User).where(User.player_id == row.player_id, User.active_title_code == row.title_code).values(active_title_code=None)
            )
            withdrawn += 1
        await session.commit()
        print(f"withdrawn: {withdrawn}")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recount the titles whose rules changed and, only when asked, withdraw those no longer earned")
    parser.add_argument("codes", nargs="*")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    sys.exit(asyncio.run(main(parser.parse_args())))
