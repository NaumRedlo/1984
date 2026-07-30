"""Who else in this chat has played this map.

The engine draws a scoreboard down the left of a render and takes the rows from
outside itself — deliberately, because who belongs in a chat is the bot's
knowledge and the renderer having an opinion about it would put two answers to
that question in the repository. This is that knowledge, in the shape the engine
reads.

Each row is a player's **best** score on the map, whatever they set it with. That
is what makes the column comparable and also what makes it a comparison of
scores rather than of plays: somebody's no-mod million sits beside a HardRock
DoubleTime run and the numbers are honest while the impression is not. So the
mods go in the row, and the engine draws them.
"""

import asyncio

from sqlalchemy import select

from db.models.user import User
from utils.logger import get_logger

logger = get_logger("services.dossier.rivals")

# osu! returns a player's scores on a map with the best first, so one is enough
# — but the API is somebody else's and an empty list is a normal answer.
_MODS_NONE = "NM"

# Enough rows to be a scoreboard and few enough to stay legible over a
# playfield. osu!'s own shows about this many.
MAX_ROWS = 8

# One request per player, and a chat can have dozens. Fired together rather than
# in turn: forty sequential lookups is most of a minute, and the render is
# waiting on it.
_CONCURRENCY = 6


def _row(name: str, score: dict) -> str | None:
    """One TSV line, or nothing when the score is not usable."""
    total = score.get("score") or score.get("total_score")
    if not total:
        return None
    accuracy = score.get("accuracy")
    # The API states accuracy as a fraction; the engine draws a percent.
    percent = f"{accuracy * 100:.2f}" if isinstance(accuracy, (int, float)) else ""
    mods = score.get("mods") or []
    if isinstance(mods, list):
        # v2 gives objects with an acronym; v1 gave plain strings.
        acronyms = "".join(
            m.get("acronym", "") if isinstance(m, dict) else str(m) for m in mods
        )
    else:
        acronyms = str(mods)
    if acronyms in ("", _MODS_NONE):
        acronyms = ""
    # Tabs, and a name is the one field that could contain one. Stripping beats
    # emitting a line that splits into the wrong columns.
    return "\t".join([name.replace("\t", " "), str(int(total)), percent, acronyms])


async def _best(client, beatmap_id: int, user) -> tuple[str, dict] | None:
    try:
        scores = await client.get_user_beatmap_scores(beatmap_id, user.osu_user_id)
    except Exception as exc:  # noqa: BLE001 — somebody else's API, many shapes
        logger.debug("no scores for %s on %s: %s", user.osu_username, beatmap_id, exc)
        return None
    if not scores:
        return None
    # Best by score, not by whatever order the API chose to answer in.
    best = max(scores, key=lambda s: s.get("score") or s.get("total_score") or 0)
    return user.osu_username, best


async def collect(client, session, chat_id: int, beatmap_id: int) -> str:
    """The scoreboard for this map in this chat, as the engine's TSV.

    Empty when nobody has a score on it, which the engine reads as "draw no
    scoreboard" — the right answer for a map nobody in the chat has touched.
    """
    if not beatmap_id:
        return ""
    players = (
        (
            await session.execute(
                select(User).where(User.chat_id == chat_id, User.osu_user_id.isnot(None))
            )
        )
        .scalars()
        .all()
    )
    if not players:
        return ""

    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(user):
        async with gate:
            return await _best(client, beatmap_id, user)

    found = [r for r in await asyncio.gather(*(one(u) for u in players)) if r]
    found.sort(key=lambda pair: pair[1].get("score") or pair[1].get("total_score") or 0, reverse=True)

    rows = []
    for name, score in found[:MAX_ROWS]:
        line = _row(name, score)
        if line:
            rows.append(line)
    logger.info(
        "scoreboard for beatmap %s in chat %s: %d of %d players",
        beatmap_id,
        chat_id,
        len(rows),
        len(players),
    )
    return "\n".join(rows)
