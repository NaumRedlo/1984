from sqlalchemy import select

from db.models.user import User
from utils.logger import get_logger

logger = get_logger("services.leaderboard.marks")


async def mark_rows(session, rows) -> None:
    rows = [row for row in rows if isinstance(row, dict)]
    try:
        missing = {int(row["user_id"]) for row in rows if row.get("user_id") and not row.get("player_id")}
        if missing:
            found = (await session.execute(
                select(User.id, User.player_id, User.osu_user_id).where(User.id.in_(missing)))).all()
            known = {user_id: (player_id, osu_id) for user_id, player_id, osu_id in found}
            for row in rows:
                if not row.get("player_id") and row.get("user_id") in known:
                    row["player_id"], osu_id = known[row["user_id"]]
                    row.setdefault("osu_user_id", osu_id)
        from services.render_farm import billing
        marks = await billing.marks_of({row.get("player_id"): row.get("osu_user_id") for row in rows
                                        if row.get("player_id") or row.get("osu_user_id")})
    except Exception as exc:
        logger.warning("subscriber marks for a leaderboard were not read: %s", exc)
        return
    for row in rows:
        mark = marks.get(row.get("player_id"))
        row["palette"] = row["mark"] = mark
