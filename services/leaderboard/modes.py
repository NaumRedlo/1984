"""The chat's players ranked by pp in one mode: osu!standard from the user rows, the others from
user_mode_stats, fetched from osu! when older than STALE."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from db.models.user import User
from db.models.user_mode_stats import UserModeStats
from services.leaderboard.service import ROWS_PER_PAGE
from utils.logger import get_logger
from utils.osu import rulesets

logger = get_logger("services.leaderboard.modes")

STALE = timedelta(hours=6)
FETCHING_AT_ONCE = 4
FETCH_BUDGET_SECONDS = 25.0


def _aware(when: Optional[datetime]) -> Optional[datetime]:
    return when.replace(tzinfo=timezone.utc) if when and when.tzinfo is None else when


async def refresh(session, client, chat_id: int, ruleset: int) -> int:
    """Fetch the chat's stale standings in a mode; what does not arrive in time keeps its old figures."""
    if ruleset == 0 or client is None:
        return 0
    ids = [row[0] for row in (await session.execute(
        select(User.osu_user_id).where(User.chat_id == chat_id, User.osu_user_id.isnot(None)))).all()]
    kept = {s.osu_user_id: s for s in (await session.execute(
        select(UserModeStats).where(UserModeStats.ruleset == ruleset, UserModeStats.osu_user_id.in_(ids)))).scalars()}
    now = datetime.now(timezone.utc)
    stale = [i for i in ids if i not in kept or now - _aware(kept[i].updated_at) > STALE]
    if not stale:
        return 0

    gate = asyncio.Semaphore(FETCHING_AT_ONCE)

    async def one(osu_id):
        async with gate:
            try:
                return osu_id, await client.get_user_data(osu_id, mode=rulesets.RULESETS[ruleset])
            except Exception as exc:
                logger.debug(f"{rulesets.RULESETS[ruleset]} stats of {osu_id}: {exc}")
                return osu_id, None

    tasks = [asyncio.ensure_future(one(i)) for i in stale]
    done, pending = await asyncio.wait(tasks, timeout=FETCH_BUDGET_SECONDS)
    for task in pending:
        task.cancel()
    fetched = 0
    for task in done:
        osu_id, data = task.result()
        if not data:
            continue
        row = kept.get(osu_id)
        if row is None:
            row = UserModeStats(osu_user_id=osu_id, ruleset=ruleset)
            session.add(row)
        row.pp = float(data.get("pp") or 0)
        row.global_rank = data.get("global_rank")
        row.country_rank = data.get("country_rank")
        row.accuracy = float(data.get("accuracy") or 0)
        row.play_count = int(data.get("play_count") or 0)
        row.updated_at = now
        fetched += 1
    if pending:
        logger.info(f"{rulesets.RULESETS[ruleset]} board: {len(pending)} standings did not arrive in time")
    return fetched


async def standings(session, chat_id: int, ruleset: int) -> list[dict]:
    """Everyone in the chat with pp in the mode, best first."""
    users = (await session.execute(
        select(User).where(User.chat_id == chat_id, User.osu_user_id.isnot(None)))).scalars().all()
    if ruleset == 0:
        figures = {u.osu_user_id: (u.player_pp, u.global_rank) for u in users}
    else:
        rows = (await session.execute(select(UserModeStats).where(
            UserModeStats.ruleset == ruleset,
            UserModeStats.osu_user_id.in_([u.osu_user_id for u in users])))).scalars()
        figures = {r.osu_user_id: (r.pp, r.global_rank) for r in rows}
    board = []
    for user in users:
        pp, rank = figures.get(user.osu_user_id, (None, None))
        if pp:
            board.append({"user": user, "pp": float(pp), "rank": rank})
    board.sort(key=lambda e: (-e["pp"], e["rank"] or 10 ** 9))
    for i, entry in enumerate(board):
        entry["position"] = i + 1
    return board


def _row(entry: dict, **extra) -> dict:
    user = entry["user"]
    pp = f"{int(round(entry['pp'])):,}".replace(",", " ")
    return {
        "position": entry["position"], "user_id": user.id, "username": user.osu_username,
        "active_title_code": user.active_title_code,
        "avatar_data": user.avatar_data, "cover_data": user.cover_data,
        "value_label": f"#{entry['rank']:,}".replace(",", " ") if entry["rank"] else "—",
        "sub_label": f"{pp}pp", "movement": None, **extra,
    }


def board(entries: list[dict], page: int, viewer_user_id: Optional[int]) -> dict:
    total_pages = max(1, (len(entries) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    shown = entries[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]
    rows = [_row(e) for e in shown]
    mine = next((e for e in entries if e["user"].id == viewer_user_id), None) if viewer_user_id else None
    return {"key": "pp", "rows": rows, "self_row": _row(mine, is_self=True) if mine else None,
            "participants": len(entries), "page": page, "total_pages": total_pages}
