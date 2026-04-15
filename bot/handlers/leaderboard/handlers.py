import asyncio

from aiogram import Router, types, F
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BufferedInputFile,
    InputMediaPhoto,
)
from sqlalchemy import select, desc, asc, func, and_

from db.models.user import User
from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.database import get_db_session
from services.image import leaderboard_gen
from utils.logger import get_logger
from utils.osu.helpers import extract_beatmap_id, get_message_context
from utils.formatting.text import escape_html
from bot.filters import TextTriggerFilter, TriggerArgs

router = Router(name="leaderboard")
logger = get_logger("handlers.leaderboard")

PAGE_SIZE = 5


def _parse_mods(mods):
    if not mods:
        return "—"
    if isinstance(mods, str):
        return mods
    if isinstance(mods, list):
        return "+" + ",".join(str(m) for m in mods if m)
    return str(mods)


async def _build_map_leaderboard(session, osu_api_client, beatmap_id: int):
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
        .join(max_pp_sq, and_(
            UserMapAttempt.user_id == max_pp_sq.c.user_id,
            UserMapAttempt.pp == max_pp_sq.c.max_pp,
        ))
        .group_by(UserMapAttempt.user_id)
        .subquery()
    )

    rows = []
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
        .where(User.osu_user_id.isnot(None))
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
        })

    beatmap = await osu_api_client.get_beatmap(beatmap_id)
    return rows, beatmap, int(total_plays or 0), int(unique_players or 0)


def _map_leaderboard_usage() -> str:
    return (
        "Использование: <code>lbm</code> или <code>leaderboardmap</code> в ответ на карточку игры.\n"
        "Поддерживаются сгенерированные карточки recent."
    )

# Category definitions

CATEGORIES = {
    "pp": {
        "label": "Performance Points",
        "btn": "PP",
    },
    "rank": {
        "label": "Global Rank",
        "btn": "Ранг",
    },
    "accuracy": {
        "label": "Accuracy",
        "btn": "Точность",
    },
    "play_count": {
        "label": "Play Count",
        "btn": "Плейкаунт",
    },
    "play_time": {
        "label": "Play Time",
        "btn": "Время",
    },
    "ranked_score": {
        "label": "Ranked Score",
        "btn": "Р. очки",
    },
    "hits_per_play": {
        "label": "Hits / Play",
        "btn": "ХПП",
    },
    "best_pp": {
        "label": "Best PP Score",
        "btn": "Топ скор",
    },
    "hp": {
        "label": "Hunter Points",
        "btn": "HP",
    },
}


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
    if key == "pp":
        return f"{int(raw):,} PP"
    if key == "rank":
        return f"#{int(raw):,}"
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


# Keyboard

def get_leaderboard_keyboard(active_key: str = "hp", page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    """3×3 category buttons + pagination row."""
    keys = list(CATEGORIES.keys())
    rows = [keys[i:i + 3] for i in range(0, len(keys), 3)]
    keyboard = []
    for row_keys in rows:
        row = []
        for k in row_keys:
            cat = CATEGORIES[k]
            label = f"• {cat['btn']} •" if k == active_key else cat["btn"]
            row.append(InlineKeyboardButton(text=label, callback_data=f"lb:{k}:{0}"))
        keyboard.append(row)

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀", callback_data=f"lb:{active_key}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="lb:noop:0"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶", callback_data=f"lb:{active_key}:{page + 1}"))
    keyboard.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# Count helpers

async def _count_for_category(session, key: str) -> int:
    """Return total number of eligible users for a category."""
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
        "rank": User.global_rank,
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


# Query builders (with offset/limit for pagination)

async def _query_standard(session, field_attr, order, offset=0, limit=PAGE_SIZE):
    """Standard single-field leaderboard query with NULL safety."""
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
    """Computed field: total_hits / play_count."""
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
    """Best single PP score per user from UserBestScore cache (deduplicated)."""
    # Subquery: max pp per user
    max_pp_sq = (
        select(
            UserBestScore.user_id,
            func.max(UserBestScore.pp).label("max_pp"),
        )
        .group_by(UserBestScore.user_id)
        .subquery()
    )
    # Subquery: pick one score row per user (the one with max pp, lowest score_id as tiebreaker)
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


# Build entries for a given category + page

async def _build_entries(session, key: str, page: int = 0):
    """Return list of dicts ready for the card generator."""
    offset = page * PAGE_SIZE
    entries = []

    if key in ("hp", "pp", "rank", "accuracy", "play_count", "play_time", "ranked_score"):
        field_map = {
            "hp": (User.hps_points, desc),
            "pp": (User.player_pp, desc),
            "rank": (User.global_rank, asc),
            "accuracy": (User.accuracy, desc),
            "play_count": (User.play_count, desc),
            "play_time": (User.play_time, desc),
            "ranked_score": (User.ranked_score, desc),
        }
        attr_map = {
            "hp": "hps_points",
            "pp": "player_pp",
            "rank": "global_rank",
            "accuracy": "accuracy",
            "play_count": "play_count",
            "play_time": "play_time",
            "ranked_score": "ranked_score",
        }
        field, order = field_map[key]
        users = await _query_standard(session, field, order, offset=offset)
        attr = attr_map[key]
        for i, u in enumerate(users, offset + 1):
            entries.append({
                "position": i, "country": u.country or "XX",
                "username": u.osu_username,
                "value": _format_value(key, getattr(u, attr)),
                "avatar_url": u.avatar_url,
                "cover_url": u.cover_url,
                "avatar_data": u.avatar_data,
                "cover_data": u.cover_data,
                "player_pp": u.player_pp or 0,
                "accuracy": u.accuracy or 0.0,
                "osu_user_id": u.osu_user_id,
            })

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
            })

    return entries


# Generate card for a category + page

async def _generate_card(session, key: str, page: int = 0):
    cat = CATEGORIES[key]
    total = await _count_for_category(session, key)
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(page, total_pages - 1)

    entries = await _build_entries(session, key, page)
    buf = await leaderboard_gen.generate_leaderboard_card_async(cat["label"], entries)
    photo = BufferedInputFile(buf.read(), filename=f"leaderboard_{key}.png")
    return photo, page, total_pages


# Handlers

@router.message(TextTriggerFilter("leaderboard", "lb", "top"))
async def show_leaderboard(message: types.Message, trigger_args: TriggerArgs = None):
    async with get_db_session() as session:
        try:
            photo, page, total_pages = await _generate_card(session, "pp", 0)
            await message.answer_photo(
                photo=photo,
                reply_markup=get_leaderboard_keyboard("pp", page, total_pages),
            )
        except Exception as e:
            logger.error(f"Error in /leaderboard: {e}", exc_info=True)
            await message.answer("Произошла ошибка при загрузке таблицы лидеров.")


@router.message(TextTriggerFilter("leaderboardmap", "lbm"))
async def show_map_leaderboard(message: types.Message, trigger_args: TriggerArgs = None):
    await message.answer("lbm временно отключён.")
    return


async def show_map_leaderboard_old(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None):
    return


@router.message(TextTriggerFilter("lbm_disabled"))
async def show_map_leaderboard_disabled(message: types.Message, trigger_args: TriggerArgs = None):
    await message.answer("lbm временно отключён.")
    return
    reply = message.reply_to_message
    if not reply:
        await message.answer(_map_leaderboard_usage(), parse_mode="HTML")
        return

    context = get_message_context(reply.chat.id, reply.message_id)
    beatmap_id = None
    map_title = None
    map_version = None
    if context:
        beatmap_id = context.get("beatmap_id") or context.get("beatmap")
        map_title = context.get("artist") and context.get("title") and f"{context.get('artist')} - {context.get('title')}" or context.get("title")
        map_version = context.get("version")

    if not beatmap_id:
        probe = reply.caption or reply.text or ""
        beatmap_id = extract_beatmap_id(probe)

    if not beatmap_id:
        await message.answer(_map_leaderboard_usage(), parse_mode="HTML")
        return

    async with get_db_session() as session:
        try:
            rows, beatmap, total_plays, unique_players = await _build_map_leaderboard(session, osu_api_client, int(beatmap_id))
            beatmap = beatmap or {}
            beatmapset = beatmap.get("beatmapset") or {}
            map_title = map_title or f"{beatmapset.get('artist', 'Unknown')} - {beatmapset.get('title', 'Unknown')}"
            map_version = map_version or beatmap.get("version", "Unknown")
            data = {
                "map_title": map_title,
                "map_version": map_version,
                "beatmap_id": int(beatmap_id),
                "beatmap_cover_url": beatmapset.get("covers", {}).get("cover@2x")
                    or beatmapset.get("covers", {}).get("list@2x")
                    or beatmapset.get("covers", {}).get("cover"),
                "mapper_name": beatmapset.get("creator", "Unknown"),
                "mapper_id": beatmapset.get("user_id", 0),
                "star_rating": beatmap.get("difficulty_rating", 0.0) or 0.0,
                "bpm": beatmap.get("bpm", 0.0) or 0.0,
                "total_length": beatmap.get("total_length", 0) or 0,
                "total_plays": total_plays,
                "unique_players": unique_players,
                "top_pp": rows[0].get("pp", 0) if rows else 0,
                "avg_acc": sum((row.get("accuracy", 0) or 0) for row in rows) / max(len(rows), 1),
                "rows": rows,
            }

            try:
                photo = await leaderboard_gen.generate_map_leaderboard_card_async(data)
                await message.answer_photo(photo=BufferedInputFile(photo.read(), filename="map_leaderboard.png"))
            except Exception as img_err:
                logger.warning(f"Map leaderboard card generation failed: {img_err}")
                text = [
                    f"<b>Map leaderboard</b> — {escape_html(map_title or 'Unknown map')}",
                    f"Beatmap ID: <code>{int(beatmap_id):,}</code>",
                    f"<b>PLAYS:</b> {int(total_plays):,}",
                ]
                if rows:
                    text.append("\n<b>Top players:</b>")
                    for row in rows[:10]:
                        text.append(f"#{row['position']} {escape_html(row['username'])} — {row['value']}")
                else:
                    text.append("\nЭту карту ещё не сыграл ни один зарегистрированный пользователь.")
                await message.answer("\n".join(text), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /lbm: {e}", exc_info=True)
            await message.answer("Не удалось построить leaderboard по карте.")


@router.callback_query(F.data.startswith("lb:"))
async def leaderboard_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    _, key, page_str = parts

    if key == "noop":
        await callback.answer()
        return

    if key not in CATEGORIES:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    try:
        page = max(int(page_str), 0)
    except ValueError:
        page = 0

    async with get_db_session() as session:
        try:
            photo, page, total_pages = await _generate_card(session, key, page)
            media = InputMediaPhoto(media=photo)
            await callback.message.edit_media(
                media=media,
                reply_markup=get_leaderboard_keyboard(key, page, total_pages),
            )
        except Exception as e:
            logger.error(f"Error in leaderboard callback '{key}' page {page}: {e}", exc_info=True)
            await callback.answer("Ошибка при обновлении лидерборда", show_alert=True)
            return

    await callback.answer()


__all__ = ["router"]
