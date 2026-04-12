from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
from zoneinfo import ZoneInfo

from aiogram import Router, types, F
from aiogram.types import (
    BufferedInputFile, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto,
)
from sqlalchemy import select

from config.settings import TIMEZONE
from db.database import get_db_session
from db.models.best_score import UserBestScore
from services.image_generator import card_renderer
from utils.logger import get_logger
from utils.hp_calculator import get_next_rank_info
from utils.resolve_user import get_registered_user
from bot.filters import TextTriggerFilter, TriggerArgs

router = Router(name="profile")
logger = get_logger("handlers.profile")

AUTO_UPDATE_HOURS = 6

PAGE_NAMES = ["Инфо", "Ранк", "Плейкаунт", "Топ", "Последние"]


def format_msk_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "Никогда"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    tz = ZoneInfo(TIMEZONE)
    local_time = dt.astimezone(tz)
    return local_time.strftime("%d.%m.%Y %H:%M")


def _format_play_time(seconds: int) -> str:
    if not seconds or seconds <= 0:
        return "—"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d {hours}h"


def _build_profile_keyboard(osu_user_id: int, active_page: int, invoker_tg_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for i, name in enumerate(PAGE_NAMES):
        label = f"• {name} •" if i == active_page else name
        cb_data = f"profile:{osu_user_id}:{i}:{invoker_tg_id}"
        buttons.append(InlineKeyboardButton(text=label, callback_data=cb_data))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


async def _build_page_data(
    page: int, user, osu_api_client, session,
) -> Dict:
    """Build data dict for the requested profile page."""
    hp = user.hps_points or 0
    rank_info = get_next_rank_info(hp)

    base = {
        "username": user.osu_username,
        "osu_id": user.osu_user_id,
        "pp": user.player_pp or 0,
        "global_rank": user.global_rank or 0,
        "country": user.country or "—",
        "accuracy": user.accuracy or 0.0,
        "play_count": user.play_count or 0,
        "play_time": _format_play_time(user.play_time or 0),
        "ranked_score": user.ranked_score or 0,
        "total_hits": user.total_hits or 0,
        "total_score": getattr(user, "total_score", 0) or 0,
        "hp_points": hp,
        "hp_rank": rank_info["current"],
        "next_rank": rank_info.get("next"),
        "hp_needed": rank_info.get("hp_needed", 0),
        "avatar_url": user.avatar_url,
        "cover_url": user.cover_url,
        "bounties_participated": user.bounties_participated or 0,
    }

    if page in (0, 1, 2):
        # Fetch extended data for graphs + level info
        try:
            ext = await osu_api_client.get_user_extended_data(user.osu_user_id)
        except Exception:
            ext = None
        if ext:
            base["rank_history"] = ext.get("rank_history", [])
            base["monthly_playcounts"] = ext.get("monthly_playcounts", [])
            base["level"] = ext.get("level", 0)
            base["level_progress"] = ext.get("level_progress", 0)
            base["country_rank"] = ext.get("country_rank") or 0
            if ext.get("total_score"):
                base["total_score"] = ext["total_score"]
            # Update avatar/cover from fresh API data
            base["avatar_url"] = ext.get("avatar_url") or base["avatar_url"]
            base["cover_url"] = ext.get("cover_url") or base["cover_url"]

        # Best PP from DB (top 1 score), with API fallback
        if page == 0:
            from sqlalchemy import func
            stmt = (
                select(func.max(UserBestScore.pp))
                .where(UserBestScore.user_id == user.id)
            )
            result = await session.execute(stmt)
            best_pp = result.scalar()
            if not best_pp:
                try:
                    top1 = await osu_api_client.get_user_best_scores(user.osu_user_id, limit=1)
                    if top1 and isinstance(top1, list) and top1[0].get("pp"):
                        best_pp = top1[0]["pp"]
                except Exception:
                    pass
            base["best_pp"] = best_pp or 0

    elif page == 3:
        # Top 5 scores from DB
        stmt = (
            select(UserBestScore)
            .where(UserBestScore.user_id == user.id)
            .order_by(UserBestScore.pp.desc())
            .limit(5)
        )
        result = await session.execute(stmt)
        scores = result.scalars().all()

        # Resolve missing beatmapset_id and creator via API and persist
        for s in scores:
            if (not s.beatmapset_id or not s.creator) and s.beatmap_id:
                bm = await osu_api_client.get_beatmap(s.beatmap_id)
                if bm:
                    if not s.beatmapset_id and bm.get("beatmapset_id"):
                        s.beatmapset_id = bm["beatmapset_id"]
                    beatmapset = bm.get("beatmapset") or {}
                    if not s.creator and beatmapset.get("creator"):
                        s.creator = beatmapset["creator"]
        await session.commit()

        base["top_scores"] = [
            {
                "rank": s.rank or "F",
                "artist": s.artist or "",
                "title": s.title or "",
                "version": s.version or "",
                "pp": s.pp or 0,
                "accuracy": s.accuracy or 0,
                "max_combo": s.max_combo or 0,
                "mods": s.mods or "",
                "beatmap_id": s.beatmap_id or 0,
                "beatmapset_id": s.beatmapset_id or 0,
                "creator": s.creator or "",
            }
            for s in scores
        ]

    elif page == 4:
        # Recent plays from API
        recent = await osu_api_client.get_user_recent_scores(user.osu_user_id, limit=5)
        base["recent_scores"] = recent

    return base


@router.message(TextTriggerFilter("profile"))
async def show_profile(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None):
    tg_id = message.from_user.id

    if not osu_api_client:
        await message.answer("Ошибка: API-клиент не инициализирован.")
        return

    async with get_db_session() as session:
        try:
            user = await get_registered_user(session, tg_id)

            if not user:
                await message.answer(
                    "Вы не зарегистрированы.\n"
                    "Используйте <code>register &lt;osu_nickname&gt;</code>",
                    parse_mode="HTML"
                )
                return

            # Auto-update if stale
            now_utc = datetime.now(timezone.utc)
            last_update = user.last_api_update.replace(tzinfo=timezone.utc) if user.last_api_update else None

            if last_update is None or (now_utc - last_update) > timedelta(hours=AUTO_UPDATE_HOURS):
                wait_msg = await message.answer("Загрузка свежих данных из osu!...")
                success = await osu_api_client.sync_user_stats_from_api(user)
                if success:
                    await osu_api_client.sync_user_best_scores(user, session)
                    await session.commit()
                    await session.refresh(user)
                    await wait_msg.delete()
                else:
                    await wait_msg.edit_text("Не удалось получить данные из osu! API. Показаны кешированные данные.")

            # Generate page 0
            try:
                data = await _build_page_data(0, user, osu_api_client, session)
                buf = await card_renderer.generate_profile_page_async(0, data)
                photo = BufferedInputFile(buf.read(), filename="profile.png")
                keyboard = _build_profile_keyboard(user.osu_user_id, 0, tg_id)
                await message.answer_photo(photo=photo, reply_markup=keyboard)
            except Exception as img_err:
                logger.warning(f"Profile card generation failed: {img_err}", exc_info=True)
                await message.answer("Ошибка генерации карточки профиля.")

        except Exception as e:
            logger.error(f"Error in /profile for {tg_id}: {e}", exc_info=True)
            await message.answer("Произошла ошибка при загрузке профиля.")


@router.callback_query(F.data.startswith("profile:"))
async def profile_page_callback(callback: CallbackQuery, osu_api_client):
    try:
        parts = callback.data.split(":")
        if len(parts) != 4:
            await callback.answer("Неверный формат данных")
            return

        osu_user_id = int(parts[1])
        page = int(parts[2])
        invoker_tg_id = int(parts[3])

        if callback.from_user.id != invoker_tg_id:
            await callback.answer("Это не ваш профиль!", show_alert=True)
            return

        if page < 0 or page >= len(PAGE_NAMES):
            await callback.answer("Неверная страница")
            return

        async with get_db_session() as session:
            user = await get_registered_user(session, callback.from_user.id)
            if not user:
                await callback.answer("Пользователь не найден")
                return

            data = await _build_page_data(page, user, osu_api_client, session)
            buf = await card_renderer.generate_profile_page_async(page, data)
            photo = BufferedInputFile(buf.read(), filename=f"profile_p{page}.png")
            keyboard = _build_profile_keyboard(osu_user_id, page, invoker_tg_id)

            await callback.message.edit_media(
                media=InputMediaPhoto(media=photo),
                reply_markup=keyboard,
            )
            await callback.answer()

    except Exception as e:
        logger.error(f"Error in profile callback (page={callback.data}): {e}", exc_info=True)
        await callback.answer("Ошибка загрузки страницы")


@router.message(TextTriggerFilter("refresh"))
async def refresh_profile(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None):
    tg_id = message.from_user.id

    if not osu_api_client:
        await message.answer("Ошибка: API-клиент не инициализирован.")
        return

    wait_msg = None
    async with get_db_session() as session:
        try:
            user = await get_registered_user(session, tg_id)

            if not user:
                await message.answer(
                    "Вы не зарегистрированы.\n"
                    "Используйте <code>register &lt;osu_nickname&gt;</code>",
                    parse_mode="HTML"
                )
                return

            wait_msg = await message.answer(
                "Загрузка данных из osu! API...\n\n<i>Это может занять несколько секунд</i>",
                parse_mode="HTML"
            )

            success = await osu_api_client.sync_user_stats_from_api(user)

            if success:
                await osu_api_client.sync_user_best_scores(user, session)
                await session.commit()
                await session.refresh(user)
                await wait_msg.edit_text(
                    "<b>Данные успешно обновлены!</b>",
                    parse_mode="HTML"
                )
            else:
                await wait_msg.edit_text("Не удалось обновить данные. Попробуйте позже.", parse_mode="HTML")

        except Exception as e:
            logger.error(f"Unhandled exception in /refresh for {tg_id}: {e}", exc_info=True)
            error_text = "Произошла ошибка при обновлении. Проверьте логи."
            if wait_msg:
                await wait_msg.edit_text(error_text)
            else:
                await message.answer(error_text)

__all__ = ["router"]
