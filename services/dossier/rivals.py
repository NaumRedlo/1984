import asyncio
import io
import os
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select

from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from utils.logger import get_logger

logger = get_logger("services.dossier.rivals")

_MODS_NONE = "NM"

_HAS_LEADERBOARD = frozenset({"ranked", "approved", "qualified", "loved"})

def has_leaderboard(beatmap: dict | None) -> bool:
    status = ((beatmap or {}).get("status") or "").strip().lower()
    return status in _HAS_LEADERBOARD if status else True

MAX_ROWS = 8

_CONCURRENCY = 6

_LEGACY_FIELDS = ("legacy_total_score", "score")
_LAZER_FIELDS = ("total_score", "score")

def _total(score: dict, lazer: bool) -> int | None:
    for field in _LAZER_FIELDS if lazer else _LEGACY_FIELDS:
        value = score.get(field)
        if value:
            return int(value)
    return None

_AVATAR_PX = 128
_COVER_PX = (512, 160)

def _as_png(blob: bytes | None, into: str, name: str, box) -> str | None:
    if not blob:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as image:
            image = image.convert("RGBA")
            if isinstance(box, tuple):
                image = image.resize(box, Image.LANCZOS)
            else:

                side = min(image.size)
                left = (image.width - side) // 2
                top = (image.height - side) // 2
                image = image.crop((left, top, left + side, top + side)).resize(
                    (box, box), Image.LANCZOS
                )
            path = os.path.join(into, name)
            image.save(path, "PNG")
            return path
    except Exception as exc:
        logger.debug("could not convert %s: %s", name, exc)
        return None

def pictures_for(user, into: str, tag: str) -> tuple[str | None, str | None]:
    safe = "".join(c for c in tag if c.isalnum() or c in "-_")[:32] or "player"
    return (
        _as_png(getattr(user, "avatar_data", None), into, f"av-{safe}.png", _AVATAR_PX),
        _as_png(getattr(user, "cover_data", None), into, f"cv-{safe}.png", _COVER_PX),
    )

async def ensure_pictures(client, session, players) -> None:
    if not client:
        return
    wanted = [
        (user, field, url)
        for user in players
        for field, url in (
            ("avatar_data", getattr(user, "avatar_url", None)),
            ("cover_data", getattr(user, "cover_url", None)),
        )
        if url and not getattr(user, field, None)
    ]
    if not wanted:
        return

    async def fetch(user, field, url):
        try:
            data = await client._download_image_bytes(url)
        except Exception as exc:
            logger.debug("could not fetch %s for %s: %s", field, user.osu_username, exc)
            return
        if data:
            setattr(user, field, data)

    await asyncio.gather(*(fetch(*item) for item in wanted))
    try:
        await session.commit()
    except Exception as exc:
        logger.debug("could not cache the pictures: %s", exc)

def _row(
    name: str,
    score: dict,
    lazer: bool = True,
    avatar: str | None = None,
    cover: str | None = None,
) -> str | None:
    total = _total(score, lazer)
    if not total:
        return None
    accuracy = score.get("accuracy")

    percent = f"{accuracy * 100:.2f}" if isinstance(accuracy, (int, float)) else ""
    mods = score.get("mods") or []
    if isinstance(mods, list):

        acronyms = "".join(
            m.get("acronym", "") if isinstance(m, dict) else str(m) for m in mods
        )
    else:
        acronyms = str(mods)
    if acronyms in ("", _MODS_NONE):
        acronyms = ""

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
    except Exception as exc:
        logger.debug("no scores for %s on %s: %s", user.osu_username, beatmap_id, exc)
        return None
    if not scores:
        return None

    best = max(scores, key=lambda s: _total(s, lazer) or 0)
    return user, best

async def _from_our_own_records(session, players, beatmap_id: int) -> dict[int, dict]:
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

async def plays_here(session, chat_id: int, player: str | None) -> bool:
    if not player or not player.strip():
        return False
    found = await session.execute(
        select(User.id).where(
            User.chat_id == chat_id,
            func.lower(User.osu_username) == player.strip().lower(),
        )
    )
    return found.scalars().first() is not None

async def collect(
    client,
    session,
    chat_id: int,
    beatmap_id: int,
    status: str | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    lazer: bool = True,
    pictures_into: str | None = None,
    player: str | None = None,
) -> str:
    if not beatmap_id:
        return ""
    if not await plays_here(session, chat_id, player):
        logger.info("%s is not in chat %s — no scoreboard", player or "the player", chat_id)
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

    found = found[:MAX_ROWS]

    if pictures_into:
        await ensure_pictures(client, session, [user for user, _ in found])

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
