import asyncio

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
    try:
        titled = await billing.title_palettes_of({row.get("player_id"): row.get("osu_user_id") for row in rows
                                                  if row.get("player_id") or row.get("osu_user_id")})
    except Exception as exc:
        logger.warning("subscriber title colours for a leaderboard were not read: %s", exc)
        return
    for row in rows:
        row["title_palette"] = titled.get(row.get("player_id"))


_flags: dict = {}


async def _flag(url):
    if url in _flags:
        return _flags[url]
    from services.image.utils import download_image
    picture = await download_image(url)
    if picture is not None:
        if len(_flags) > 256:
            _flags.clear()
        _flags[url] = picture
    return picture


async def team_rows(session, rows) -> None:
    rows = [row for row in rows if isinstance(row, dict) and row.get("user_id")]
    if not rows:
        return
    try:
        found = (await session.execute(
            select(User.id, User.team_tag, User.team_flag_url).where(User.id.in_({int(row["user_id"]) for row in rows})))).all()
        teams = {user_id: (tag, url) for user_id, tag, url in found if url}
        urls = sorted({url for _, url in teams.values()})
        pictures = dict(zip(urls, await asyncio.gather(*(_flag(url) for url in urls), return_exceptions=True)))
    except Exception as exc:
        logger.warning("teams for a leaderboard were not read: %s", exc)
        return
    for row in rows:
        tag, url = teams.get(row["user_id"], (None, None))
        picture = pictures.get(url)
        row["team"] = {"short_name": tag, "flag": picture} if url and not isinstance(picture, BaseException) and picture is not None else None
