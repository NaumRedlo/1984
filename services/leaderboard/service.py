import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from aiogram.types import BufferedInputFile
from sqlalchemy import select, desc, asc, func, and_

from db.models.user import User
from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.database import get_db_session
from services.image import leaderboard_gen
from services.refresh import refresh_user, is_stale, STALE_THRESHOLD
from utils.logger import get_logger

logger = get_logger("services.leaderboard")

PAGE_SIZE = 5
SYNC_COOLDOWN = timedelta(minutes=5)
_sync_cooldown: dict[int, datetime] = {}  # beatmap_id -> last sync time
_pending_stale_ids: set[int] = set()
_stale_refresh_task: asyncio.Task[None] | None = None


CATEGORIES: dict[str, dict[str, str]] = {
    "pp": {"label": "PP & Rank", "btn": "PP/Ранг"},
    "accuracy": {"label": "Accuracy", "btn": "Точность"},
    "play_count": {"label": "Play Count", "btn": "Плейкаунт"},
    "play_time": {"label": "Play Time", "btn": "Время"},
    "ranked_score": {"label": "Ranked Score", "btn": "Р. очки"},
    "hits_per_play": {"label": "Hits / Play", "btn": "ХПП"},
    "best_pp": {"label": "Best PP Score", "btn": "Топ скор"},
    "hp": {"label": "Hunter Points", "btn": "HP"},
}


def schedule_stale_refresh(entries: list[dict[str, Any]], osu_api_client) -> None:
    """Fire-and-forget: refresh stale users shown on the leaderboard."""
    global _stale_refresh_task

    stale_ids: list[int] = []
    for e in entries:
        uid = e.get("osu_user_id")
        last = e.get("last_api_update")
        if uid and is_stale(last, STALE_THRESHOLD):
            stale_ids.append(uid)
    if not stale_ids or not osu_api_client:
        return

    _pending_stale_ids.update(stale_ids)
    if _stale_refresh_task and not _stale_refresh_task.done():
        return

    async def _refresh():
        global _stale_refresh_task
        try:
            while _pending_stale_ids:
                osu_uid = _pending_stale_ids.pop()
                try:
                    async with get_db_session() as session:
                        user = (await session.execute(
                            select(User).where(User.osu_user_id == osu_uid)
                        )).scalar_one_or_none()
                        if user:
                            ok = await refresh_user(user, session, osu_api_client, mode="stats_only")
                            if ok:
                                await session.commit()
                                logger.debug(f"Leaderboard refresh done: {user.osu_username}")
                except Exception as exc:
                    logger.debug(f"Leaderboard refresh failed for osu_uid={osu_uid}: {exc}")
        finally:
            _stale_refresh_task = None

    _stale_refresh_task = asyncio.create_task(_refresh())


def _format_play_time(seconds: int) -> str:
    if seconds is None or seconds <= 0:
        return "—"
    hours = seconds // 3600
    return f"{hours}h"


def _format_value(key: str, raw, extra: str = "") -> str:
    if raw is None:
        return "—"
    if key == "hp":
        return f"{int(raw):,} HP"
    if key == "accuracy":
        return f"{float(raw):.2f}%"
    if key == "play_count":
        return f"{int(raw):,}"
    if key == "play_time":
        return _format_play_time(int(raw))
    if key == "ranked_score":
        return f"{int(raw):,}"
    if key == "hits_per_play":
        return f"{float(raw):,.1f}"
    if key == "best_pp":
        pp_str = f"{float(raw):.0f}pp"
        if extra:
            return f"{pp_str} — {extra}"
        return pp_str
    return str(raw)


async def _count_for_category(session, key: str) -> int:
    if key == "best_pp":
        stmt = select(func.count(func.distinct(UserBestScore.user_id)))
        result = await session.execute(stmt)
        return result.scalar() or 0

    if key == "hits_per_play":
        stmt = (
            select(func.count())
            .select_from(User)
            .where(
                User.osu_user_id.isnot(None),
                User.play_count.isnot(None), User.play_count > 0,
                User.total_hits.isnot(None), User.total_hits > 0,
            )
        )
        result = await session.execute(stmt)
        return result.scalar() or 0

    field_map = {
        "hp": User.hps_points,
        "pp": User.player_pp,
        "accuracy": User.accuracy,
        "play_count": User.play_count,
        "play_time": User.play_time,
        "ranked_score": User.ranked_score,
    }
    field = field_map[key]
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.osu_user_id.isnot(None), field.isnot(None), field > 0)
    )
    result = await session.execute(stmt)
    return result.scalar() or 0


async def _query_standard(session, field_attr, order, offset=0, limit=PAGE_SIZE):
    stmt = (
        select(User)
        .where(User.osu_user_id.isnot(None), field_attr.isnot(None), field_attr > 0)
        .order_by(order(field_attr))
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def _query_hits_per_play(session, offset=0, limit=PAGE_SIZE):
    ratio = (User.total_hits * 1.0 / User.play_count).label("hits_ratio")
    stmt = (
        select(User, ratio)
        .where(
            User.osu_user_id.isnot(None),
            User.play_count.isnot(None), User.play_count > 0,
            User.total_hits.isnot(None), User.total_hits > 0,
        )
        .order_by(desc(ratio))
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.all()


async def _query_best_pp(session, offset=0, limit=PAGE_SIZE):
    max_pp_sq = (
        select(
            UserBestScore.user_id,
            func.max(UserBestScore.pp).label("max_pp"),
        )
        .group_by(UserBestScore.user_id)
        .subquery()
    )
    min_score_sq = (
        select(
            UserBestScore.user_id,
            func.min(UserBestScore.score_id).label("pick_id"),
        )
        .join(
            max_pp_sq,
            and_(
                UserBestScore.user_id == max_pp_sq.c.user_id,
                UserBestScore.pp == max_pp_sq.c.max_pp,
            ),
        )
        .group_by(UserBestScore.user_id)
        .subquery()
    )
    stmt = (
        select(
            User,
            UserBestScore.pp,
            UserBestScore.artist,
            UserBestScore.title,
            UserBestScore.version,
        )
        .join(UserBestScore, and_(
            User.id == UserBestScore.user_id,
            UserBestScore.score_id == min_score_sq.c.pick_id,
        ))
        .join(min_score_sq, User.id == min_score_sq.c.user_id)
        .order_by(desc(UserBestScore.pp))
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.all()


async def _build_entries(session, key: str, page: int = 0) -> list[dict[str, Any]]:
    offset = page * PAGE_SIZE
    entries: list[dict[str, Any]] = []

    if key in ("hp", "pp", "accuracy", "play_count", "play_time", "ranked_score"):
        field_map = {
            "hp": (User.hps_points, desc),
            "pp": (User.player_pp, desc),
            "accuracy": (User.accuracy, desc),
            "play_count": (User.play_count, desc),
            "play_time": (User.play_time, desc),
            "ranked_score": (User.ranked_score, desc),
        }
        attr_map = {
            "hp": "hps_points",
            "pp": "player_pp",
            "accuracy": "accuracy",
            "play_count": "play_count",
            "play_time": "play_time",
            "ranked_score": "ranked_score",
        }
        field, order = field_map[key]
        users = await _query_standard(session, field, order, offset=offset)
        attr = attr_map[key]
        for i, u in enumerate(users, offset + 1):
            entry: dict[str, Any] = {
                "position": i, "country": u.country or "XX",
                "username": u.osu_username,
                "avatar_url": u.avatar_url,
                "cover_url": u.cover_url,
                "avatar_data": u.avatar_data,
                "cover_data": u.cover_data,
                "player_pp": u.player_pp or 0,
                "accuracy": u.accuracy or 0.0,
                "osu_user_id": u.osu_user_id,
                "last_api_update": u.last_api_update,
            }
            if key == "pp":
                rank_val = u.global_rank or 0
                entry["value"] = f"#{rank_val:,}"
                entry["sub_value"] = f"{int(u.player_pp or 0):,}pp"
            else:
                entry["value"] = _format_value(key, getattr(u, attr))
            entries.append(entry)

    elif key == "hits_per_play":
        rows = await _query_hits_per_play(session, offset=offset)
        for i, (u, ratio) in enumerate(rows, offset + 1):
            entries.append({
                "position": i, "country": u.country or "XX",
                "username": u.osu_username,
                "value": _format_value(key, ratio),
                "avatar_url": u.avatar_url,
                "cover_url": u.cover_url,
                "avatar_data": u.avatar_data,
                "cover_data": u.cover_data,
                "player_pp": u.player_pp or 0,
                "accuracy": u.accuracy or 0.0,
                "osu_user_id": u.osu_user_id,
                "last_api_update": u.last_api_update,
            })

    elif key == "best_pp":
        rows = await _query_best_pp(session, offset=offset)
        for i, (user, pp_val, artist, title, version) in enumerate(rows, offset + 1):
            map_name = f"{artist} - {title}" if artist else title or ""
            if version:
                map_name += f" [{version}]"
            if len(map_name) > 35:
                map_name = map_name[:32] + "..."
            entries.append({
                "position": i, "country": user.country or "XX",
                "username": user.osu_username,
                "value": _format_value(key, pp_val, extra=map_name),
                "avatar_url": user.avatar_url,
                "cover_url": user.cover_url,
                "avatar_data": user.avatar_data,
                "cover_data": user.cover_data,
                "player_pp": user.player_pp or 0,
                "accuracy": user.accuracy or 0.0,
                "osu_user_id": user.osu_user_id,
                "last_api_update": user.last_api_update,
            })

    return entries


async def build_category_card(session, key: str, page: int = 0):
    cat = CATEGORIES[key]
    total = await _count_for_category(session, key)
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(page, total_pages - 1)

    entries = await _build_entries(session, key, page)
    buf = await leaderboard_gen.generate_leaderboard_card_async(cat["label"], entries)
    photo = BufferedInputFile(buf.read(), filename=f"leaderboard_{key}.png")
    return photo, page, total_pages, entries


def map_leaderboard_usage() -> str:
    return (
        "Использование:\n"
        "• <code>lbm</code> — в ответ на карточку recent\n"
        "• <code>lbm 123456</code> — по ID карты\n"
        "• <code>lbm https://osu.ppy.sh/beatmaps/...</code> — по ссылке"
    )


def _parse_mods(mods) -> str:
    if not mods:
        return "—"
    if isinstance(mods, str):
        return mods
    if isinstance(mods, list):
        return "+" + ",".join(str(m) for m in mods if m)
    return str(mods)


async def _sync_beatmap_scores(session, osu_api_client, beatmap_id: int) -> None:
    now = datetime.now(timezone.utc)
    last_sync = _sync_cooldown.get(beatmap_id)
    if last_sync and (now - last_sync) < SYNC_COOLDOWN:
        return
    _sync_cooldown[beatmap_id] = now

    # Fetch public leaderboard scores (works with client_credentials, no OAuth needed)
    public_scores = await osu_api_client.get_beatmap_scores(beatmap_id, limit=50)
    if not public_scores:
        await _sync_remaining_user_scores(session, osu_api_client, beatmap_id)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
        return

    # Build osu_user_id → User map for registered users
    stmt = select(User).where(User.osu_user_id.isnot(None))
    users = {u.osu_user_id: u for u in (await session.execute(stmt)).scalars().all()}

    # Group scores by user_id
    scores_by_user: dict[int, list] = {}
    for s in public_scores:
        uid = (s.get("user_id") or (s.get("user") or {}).get("id"))
        if uid:
            scores_by_user.setdefault(int(uid), []).append(s)

    for osu_uid, user_scores in scores_by_user.items():
        user_model = users.get(osu_uid)
        if not user_model:
            continue
        for s in user_scores:
            # Public endpoint may return beatmap/beatmapset as null
            if not s.get("beatmap"):
                s["beatmap"] = {"id": beatmap_id}
            if not s.get("beatmapset"):
                s["beatmapset"] = {}
        try:
            await osu_api_client.sync_user_map_attempts(user_model, session, user_scores)
        except Exception:
            pass

    # Also sync remaining registered users (OAuth with their token, non-OAuth with client_credentials)
    await _sync_remaining_user_scores(session, osu_api_client, beatmap_id, skip_osu_ids=set(scores_by_user.keys()))

    try:
        await session.commit()
    except Exception:
        await session.rollback()


async def _sync_remaining_user_scores(session, osu_api_client, beatmap_id: int, skip_osu_ids: set = None) -> None:
    """Sync per-user scores for all registered users not already covered by the public top-50.

    OAuth users: fetched with their personal token (can see all scores).
    Non-OAuth users: fetched with client_credentials (public scores only).
    """
    from services.oauth.token_manager import get_valid_token
    from db.models.oauth_token import OAuthToken

    oauth_user_ids = set((await session.execute(
        select(OAuthToken.user_id)
    )).scalars().all())

    stmt = select(User).where(User.osu_user_id.isnot(None))
    all_users = (await session.execute(stmt)).scalars().all()

    for user_model in all_users:
        if skip_osu_ids and user_model.osu_user_id in skip_osu_ids:
            continue
        try:
            token = None
            if user_model.id in oauth_user_ids:
                token = await get_valid_token(user_model.id)
            scores = await osu_api_client.get_user_beatmap_scores(
                beatmap_id, user_model.osu_user_id, oauth_token=token
            )
        except Exception:
            continue
        if not scores:
            continue
        for s in scores:
            if not s.get("beatmap"):
                s["beatmap"] = {"id": beatmap_id}
            if not s.get("beatmapset"):
                s["beatmapset"] = {}
        try:
            await osu_api_client.sync_user_map_attempts(user_model, session, scores)
        except Exception:
            pass


def _calc_lbm_total_pages(num_rows: int) -> int:
    lbm_first_page_rows = 6  # positions 4-9
    lbm_page_rows = 5
    if num_rows <= 3 + lbm_first_page_rows:
        return 1
    remaining = num_rows - 3 - lbm_first_page_rows
    return 1 + max((remaining + lbm_page_rows - 1) // lbm_page_rows, 1)


@dataclass(frozen=True)
class MapLeaderboardResult:
    data: dict[str, Any]
    beatmapset_id: int
    total_pages: int
    rows: list[dict[str, Any]]


async def build_map_leaderboard(session, osu_api_client, beatmap_id: int, *, sync: bool = True) -> MapLeaderboardResult:
    if sync:
        await _sync_beatmap_scores(session, osu_api_client, beatmap_id)

    stats_stmt = (
        select(
            func.count(UserMapAttempt.id),
            func.count(func.distinct(UserMapAttempt.user_id)),
        )
        .select_from(UserMapAttempt)
        .join(User, User.id == UserMapAttempt.user_id)
        .where(User.osu_user_id.isnot(None), UserMapAttempt.beatmap_id == beatmap_id)
    )
    stats_result = await session.execute(stats_stmt)
    total_plays, unique_players = stats_result.one()

    max_pp_sq = (
        select(
            UserMapAttempt.user_id,
            func.max(UserMapAttempt.pp).label("max_pp"),
        )
        .join(User, User.id == UserMapAttempt.user_id)
        .where(User.osu_user_id.isnot(None), UserMapAttempt.beatmap_id == beatmap_id)
        .group_by(UserMapAttempt.user_id)
        .subquery()
    )
    pick_sq = (
        select(
            UserMapAttempt.user_id,
            func.min(UserMapAttempt.id).label("pick_id"),
        )
        .where(UserMapAttempt.beatmap_id == beatmap_id)
        .join(max_pp_sq, and_(
            UserMapAttempt.user_id == max_pp_sq.c.user_id,
            UserMapAttempt.pp == max_pp_sq.c.max_pp,
        ))
        .group_by(UserMapAttempt.user_id)
        .subquery()
    )

    rows: list[dict[str, Any]] = []
    result = await session.execute(
        select(
            User,
            UserMapAttempt.pp,
            UserMapAttempt.accuracy,
            UserMapAttempt.max_combo,
            UserMapAttempt.rank,
            UserMapAttempt.mods,
        )
        .join(UserMapAttempt, UserMapAttempt.user_id == User.id)
        .join(pick_sq, pick_sq.c.pick_id == UserMapAttempt.id)
        .where(User.osu_user_id.isnot(None), UserMapAttempt.beatmap_id == beatmap_id)
        .order_by(desc(UserMapAttempt.pp), asc(UserMapAttempt.id))
    )

    for position, (user, pp, accuracy, max_combo, rank, mods) in enumerate(result.all(), start=1):
        rows.append({
            "position": position,
            "country": user.country or "XX",
            "username": user.osu_username,
            "value": f"{float(pp or 0):.0f}pp | {float(accuracy or 0.0):.2f}% | {int(max_combo or 0)}x | {_parse_mods(mods)}",
            "pp": float(pp or 0),
            "accuracy": float(accuracy or 0.0),
            "combo": int(max_combo or 0),
            "mods": _parse_mods(mods),
            "rank": rank or "F",
            "avatar_url": user.avatar_url,
            "cover_url": user.cover_url,
            "avatar_data": user.avatar_data,
            "cover_data": user.cover_data,
            "player_pp": user.player_pp or 0,
            "osu_user_id": user.osu_user_id,
            "last_api_update": user.last_api_update,
        })

    beatmap: Optional[dict[str, Any]] = await osu_api_client.get_beatmap(beatmap_id)
    beatmap = beatmap or {}
    beatmapset = beatmap.get("beatmapset") or {}
    beatmapset_id = int(beatmapset.get("id") or 0)

    map_title = f"{beatmapset.get('artist', 'Unknown')} - {beatmapset.get('title', 'Unknown')}"
    map_version = beatmap.get("version", "Unknown")

    total_pages = _calc_lbm_total_pages(len(rows))

    data = {
        "map_title": map_title,
        "map_version": map_version,
        "beatmap_id": beatmap_id,
        "beatmap_cover_url": beatmapset.get("covers", {}).get("cover@2x")
            or beatmapset.get("covers", {}).get("list@2x")
            or beatmapset.get("covers", {}).get("cover"),
        "mapper_name": beatmapset.get("creator", "Unknown"),
        "mapper_id": beatmapset.get("user_id", 0),
        "star_rating": beatmap.get("difficulty_rating", 0.0) or 0.0,
        "bpm": beatmap.get("bpm", 0.0) or 0.0,
        "total_length": beatmap.get("total_length", 0) or 0,
        "beatmap_status": beatmap.get("status", ""),
        "total_plays": int(total_plays or 0),
        "unique_players": int(unique_players or 0),
        "rows": rows,
        "page": 0,
    }

    return MapLeaderboardResult(data=data, beatmapset_id=beatmapset_id, total_pages=total_pages, rows=rows)

