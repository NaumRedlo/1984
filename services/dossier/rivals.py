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

## The currency has to match the replay

osu!'s API answers with two different scores and they are three orders of
magnitude apart. `total_score` is lazer's standardised million; `legacy_total_score`
is the original ScoreV1 total, which runs into the hundreds of millions. The
player's own row is computed by the engine in whichever arithmetic the replay was
recorded with — so a stable replay against `total_score` rivals put the player at
forty million above a field of seven hundred thousand, and the board said nothing
except that the columns disagreed about what a point is.

So the field is chosen by the replay's client, and a rival whose score cannot be
expressed in that currency is left out rather than converted. There is no honest
conversion: ScoreV1 depends on the map's difficulty multiplier and the combo
carried into every hit, and lazer's standardised score deliberately throws both
away.
"""

import asyncio
import io
import os
from collections.abc import Awaitable, Callable

from sqlalchemy import select

from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from utils.logger import get_logger

logger = get_logger("services.dossier.rivals")

# osu! returns a player's scores on a map with the best first, so one is enough
# — but the API is somebody else's and an empty list is a normal answer.
_MODS_NONE = "NM"

# Where osu! itself keeps a leaderboard. Everywhere else — graveyard, WIP,
# pending — `get_user_beatmap_scores` has nothing to return however many times
# it is asked, so the whole chat's worth of requests would be spent learning
# what the map's status already says.
_HAS_LEADERBOARD = frozenset({"ranked", "approved", "qualified", "loved"})


def has_leaderboard(beatmap: dict | None) -> bool:
    """Whether this map is one osu! keeps scores for.

    An unknown status counts as yes. Guessing "no" would silently drop the
    scoreboard on a map that has one, and the cost of guessing "yes" is a few
    requests that come back empty.
    """
    status = ((beatmap or {}).get("status") or "").strip().lower()
    return status in _HAS_LEADERBOARD if status else True

# Enough rows to be a scoreboard and few enough to stay legible over a
# playfield. osu!'s own shows about this many.
MAX_ROWS = 8

# One request per player, and a chat can have dozens.
#
# Concurrency here buys nothing and it is worth writing down why: the API client
# holds a single `_request_lock` with a 0.2s floor between calls, so every
# request is serialised however many are in flight. Forty players is forty
# round trips in a queue, which measured at about a minute — long enough that
# the render looked frozen, which is what `on_progress` below is for.
#
# The number is kept small anyway. Six in flight against a lock costs nothing
# and means a slow reply does not stall the ones behind it once the lock frees.
_CONCURRENCY = 6


# Which field carries the score, per client. Ordered: the first one present wins.
#
# `score` is last on both paths because the v1 API called it that and the local
# `UserMapAttempt` table copies whatever the sync picked — so it is a fallback of
# last resort rather than an answer.
_LEGACY_FIELDS = ("legacy_total_score", "score")
_LAZER_FIELDS = ("total_score", "score")


def _total(score: dict, lazer: bool) -> int | None:
    for field in _LAZER_FIELDS if lazer else _LEGACY_FIELDS:
        value = score.get(field)
        if value:
            return int(value)
    return None


# What the pictures are scaled to before they go to the engine.
#
# Small on purpose. The engine draws an avatar at about fifty pixels and a cover
# at three hundred, so anything larger is bytes decoded once and thrown away —
# and the decode happens while somebody is waiting for a render to start.
_AVATAR_PX = 128
_COVER_PX = (512, 160)


def _as_png(blob: bytes | None, into: str, name: str, box) -> str | None:
    """Write one cached image out as a PNG the engine can read.

    The engine has one image decoder and no network, so PNG is the only format it
    takes — and converting is this side's job because this side already has an
    imaging library and already holds every avatar it has ever seen. osu! serves
    JPEG about as often as PNG, so without this step half the rows would draw
    without a face for no reason anybody could see.
    """
    if not blob:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as image:
            image = image.convert("RGBA")
            if isinstance(box, tuple):
                image = image.resize(box, Image.LANCZOS)
            else:
                # Square, cropped from the middle: an avatar is drawn square and
                # squashing a rectangular one is worse than losing its edges.
                side = min(image.size)
                left = (image.width - side) // 2
                top = (image.height - side) // 2
                image = image.crop((left, top, left + side, top + side)).resize(
                    (box, box), Image.LANCZOS
                )
            path = os.path.join(into, name)
            image.save(path, "PNG")
            return path
    except Exception as exc:  # noqa: BLE001 — a broken image must not stop a render
        logger.debug("could not convert %s: %s", name, exc)
        return None


def pictures_for(user, into: str, tag: str) -> tuple[str | None, str | None]:
    """This player's avatar and cover as PNGs, or nothing."""
    safe = "".join(c for c in tag if c.isalnum() or c in "-_")[:32] or "player"
    return (
        _as_png(getattr(user, "avatar_data", None), into, f"av-{safe}.png", _AVATAR_PX),
        _as_png(getattr(user, "cover_data", None), into, f"cv-{safe}.png", _COVER_PX),
    )


def _row(
    name: str,
    score: dict,
    lazer: bool = True,
    avatar: str | None = None,
    cover: str | None = None,
) -> str | None:
    """One TSV line, or nothing when the score is not usable."""
    total = _total(score, lazer)
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
    return "\t".join(
        [
            name.replace("\t", " "),
            str(total),
            percent,
            acronyms,
            avatar or "",
            cover or "",
        ]
    )


async def _best(client, beatmap_id: int, user, lazer: bool) -> tuple[object, dict] | None:
    try:
        scores = await client.get_user_beatmap_scores(beatmap_id, user.osu_user_id)
    except Exception as exc:  # noqa: BLE001 — somebody else's API, many shapes
        logger.debug("no scores for %s on %s: %s", user.osu_username, beatmap_id, exc)
        return None
    if not scores:
        return None
    # Best in the currency this board is being drawn in, not by whatever order
    # the API chose to answer in — and not by the other currency either, which
    # can rank two plays differently.
    best = max(scores, key=lambda s: _total(s, lazer) or 0)
    return user, best


async def _from_our_own_records(session, players, beatmap_id: int) -> dict[int, dict]:
    """Scores the bot already knows about, without asking anybody.

    The profile sync writes every attempt it sees into `UserMapAttempt`, so a map
    the chat has played recently is often already here — and one SQL query beats
    forty round trips through a rate limiter. It is not a replacement: the table
    only holds what the sync happened to catch, so whoever is missing still has
    to be asked.
    """
    if not players:
        return {}
    rows = (
        (
            await session.execute(
                select(UserMapAttempt).where(
                    UserMapAttempt.beatmap_id == beatmap_id,
                    UserMapAttempt.user_id.in_([p.id for p in players]),
                )
            )
        )
        .scalars()
        .all()
    )
    best: dict[int, dict] = {}
    for row in rows:
        score = {
            "score": row.score,
            "accuracy": row.accuracy,
            "mods": row.mods or "",
        }
        held = best.get(row.user_id)
        if not held or (row.score or 0) > (held.get("score") or 0):
            best[row.user_id] = score
    return best


async def collect(
    client,
    session,
    chat_id: int,
    beatmap_id: int,
    status: str | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    lazer: bool = True,
    pictures_into: str | None = None,
) -> str:
    """The scoreboard for this map in this chat, as the engine's TSV.

    Empty when nobody has a score on it, which the engine reads as "draw no
    scoreboard" — the right answer for a map nobody in the chat has touched, and
    the only possible answer for one osu! keeps no scores for.
    """
    if not beatmap_id:
        return ""
    if not has_leaderboard({"status": status} if status else None):
        logger.info("beatmap %s is %s — no leaderboard to read", beatmap_id, status)
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

    # What we already have, for nothing — but only when the currencies match.
    # `UserMapAttempt.score` holds whatever the profile sync picked, which is
    # lazer's standardised total, so on a stable replay the shortcut would fill
    # the board with numbers three orders of magnitude off. Correctness first;
    # the cost is that stable replays pay the full round of lookups.
    known = await _from_our_own_records(session, players, beatmap_id) if lazer else {}
    found = [(p, known[p.id]) for p in players if p.id in known]
    to_ask = [p for p in players if p.id not in known]
    logger.info(
        "beatmap %s: %d of %d players already on record, asking about %d",
        beatmap_id,
        len(found),
        len(players),
        len(to_ask),
    )

    gate = asyncio.Semaphore(_CONCURRENCY)
    done = 0

    async def one(user):
        nonlocal done
        async with gate:
            result = await _best(client, beatmap_id, user, lazer)
        done += 1
        if on_progress:
            await on_progress(done, len(to_ask))
        return result

    found += [r for r in await asyncio.gather(*(one(u) for u in to_ask)) if r]
    found.sort(key=lambda pair: _total(pair[1], lazer) or 0, reverse=True)
    # Only the rows that will be drawn get their pictures converted. Decoding and
    # resizing an image for a row nobody sees is time somebody is waiting for.
    found = found[:MAX_ROWS]

    rows, dropped = [], 0
    for user, score in found:
        avatar, cover = (
            pictures_for(user, pictures_into, str(user.osu_user_id))
            if pictures_into
            else (None, None)
        )
        line = _row(user.osu_username, score, lazer, avatar, cover)
        if line:
            if len(rows) < MAX_ROWS:
                rows.append(line)
        else:
            # A score with no total in this currency. On a stable board that is
            # a play set in lazer, which has no ScoreV1 total and no honest
            # conversion to one.
            dropped += 1
    logger.info(
        "scoreboard for beatmap %s in chat %s: %d rows of %d players (%s scoring, %d unusable)",
        beatmap_id,
        chat_id,
        len(rows),
        len(players),
        "lazer" if lazer else "legacy",
        dropped,
    )
    return "\n".join(rows)
