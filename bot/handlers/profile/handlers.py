from typing import Optional, Dict

from aiogram import Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.database import get_db_session
from db.models.best_score import UserBestScore
from services.image import card_renderer
from utils.logger import get_logger
from utils.hp_calculator import get_next_rank_info, get_division_for_hp
from utils.osu.resolve_user import get_registered_user, get_reply_target_user, resolve_osu_query_status
from utils.formatting.text import escape_html
from utils.titles import TITLE_REGISTRY
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.common.auth import require_registered_user
from services.refresh import refresh_user, needs_blocking_refresh

router = Router(name="profile")
logger = get_logger("handlers.profile")


def _format_play_time(seconds: int) -> str:
    if not seconds or seconds <= 0:
        return "—"
    hours = seconds // 3600
    return f"{hours}h"


def _tg_handle(from_user) -> Optional[str]:
    """Telegram @handle to show on the card, or None when the user has no public
    username (the card then shows no handle at all — never the osu! name)."""
    username = getattr(from_user, "username", None) if from_user else None
    return f"@{username}" if username else None


async def _resolve_profile_user(session, osu_api_client, tg_id: int, chat_id: int, query: Optional[str] = None):
    if not query:
        user = await get_registered_user(session, tg_id, chat_id)
        return user, None, "registered" if user else "not_found"

    return await resolve_osu_query_status(session, osu_api_client, query, chat_id)


async def _build_page_data(
    user, osu_api_client, session, tg_handle: Optional[str] = None,
) -> Dict:
    """Build the full data dict for the profile dashboard card.

    `tg_handle` is the ready-to-show Telegram identity of the profile's owner
    (``@username`` when public, else the display name) from the message context;
    it's shown under the name instead of the osu! handle. None falls back to the
    osu! username in the renderer.
    """
    def _get(field: str, default=0):
        if isinstance(user, dict):
            aliases = {
                "osu_username": ["osu_username", "username"],
                "osu_user_id": ["osu_user_id", "id"],
                "player_pp": ["player_pp", "pp"],
                "global_rank": ["global_rank", "rank"],
                "country": ["country", "country_code"],
                "accuracy": ["accuracy"],
                "play_count": ["play_count"],
                "play_time": ["play_time"],
                "ranked_score": ["ranked_score"],
                "total_hits": ["total_hits"],
                "total_score": ["total_score"],
                "avatar_url": ["avatar_url"],
                "cover_url": ["cover_url"],
                "bounties_participated": ["bounties_participated"],
                "hps_points": ["hps_points", "hp_points"],
            }
            for key in aliases.get(field, [field]):
                if key in user:
                    return user.get(key, default)
            return default
        return getattr(user, field, default)

    hp = _get("hps_points", 0) or 0
    rank_info = get_next_rank_info(hp)

    base = {
        "username": _get("osu_username", "???"),
        "handle": tg_handle or None,
        "osu_id": _get("osu_user_id", 0),
        "pp": _get("player_pp", 0) or 0,
        "global_rank": _get("global_rank", 0) or 0,
        "country": _get("country", "—") or "—",
        "accuracy": _get("accuracy", 0.0) or 0.0,
        "play_count": _get("play_count", 0) or 0,
        "play_time": _format_play_time(_get("play_time", 0) or 0),
        "ranked_score": _get("ranked_score", 0) or 0,
        "total_hits": _get("total_hits", 0) or 0,
        "total_score": _get("total_score", 0) or 0,
        "hp_points": hp,
        "hp_rank": rank_info["current"],
        "hp_division": get_division_for_hp(hp),
        "next_rank": rank_info.get("next"),
        "hp_needed": rank_info.get("hp_needed", 0),
        "avatar_url": _get("avatar_url", None),
        "cover_url": _get("cover_url", None),
        "bounties_participated": _get("bounties_participated", 0) or 0,
    }

    # Active title chip — registered users only; falls back to nothing.
    base["title"] = None
    base["title_color"] = None
    base["title_outline"] = False
    if not isinstance(user, dict):
        tc = getattr(user, "active_title_code", None)
        if tc:
            td = TITLE_REGISTRY.get(tc)
            if td:
                base["title"] = td.name
                base["title_color"] = td.color
                base["title_outline"] = td.rarity in ("epic", "legendary", "mythic", "secret")

    osu_user_id = _get("osu_user_id", 0)
    is_registered = not isinstance(user, dict)

    # Extended data: graphs, level, country rank, grade counts, join/online — the
    # single dashboard needs all of it, so this is unconditional now.
    if osu_user_id:
        try:
            ext = await osu_api_client.get_user_extended_data(osu_user_id)
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
            base["country_name"] = ext.get("country_name")
            base["maximum_combo"] = ext.get("maximum_combo", 0)
            base["replays_watched"] = ext.get("replays_watched", 0)
            base["grade_counts"] = ext.get("grade_counts", {})
            base["total_maps"] = ext.get("total_maps", 0)
            base["is_online"] = ext.get("is_online", False)
            base["is_supporter"] = ext.get("is_supporter", False)
            base["join_date"] = ext.get("join_date")
            base["last_visit"] = ext.get("last_visit")
            base["avatar_url"] = ext.get("avatar_url") or base["avatar_url"]
            base["cover_url"] = ext.get("cover_url") or base["cover_url"]

        # Best PP from DB cache; API fallback only for unregistered users
        best_pp = None
        if is_registered:
            from sqlalchemy import func
            stmt = (
                select(func.max(UserBestScore.pp))
                .where(UserBestScore.user_id == user.id)
            )
            result = await session.execute(stmt)
            best_pp = result.scalar()
        if not best_pp:
            try:
                top1 = await osu_api_client.get_user_best_scores(osu_user_id, limit=1)
                if top1 and isinstance(top1, list) and top1[0].get("pp"):
                    best_pp = top1[0]["pp"]
            except Exception:
                pass
        base["best_pp"] = best_pp or 0

        # Top 5 scores — DB cache for registered users, API for everyone else.
        if is_registered:
            stmt = (
                select(UserBestScore)
                .where(UserBestScore.user_id == user.id)
                .order_by(UserBestScore.pp.desc())
                .limit(5)
            )
            result = await session.execute(stmt)
            scores = result.scalars().all()

            # Resolve missing beatmapset_id / creator via API and persist
            for s in scores:
                if (not s.beatmapset_id or not s.creator) and s.beatmap_id:
                    bm = await osu_api_client.get_beatmap(s.beatmap_id)
                    if bm:
                        if not s.beatmapset_id and bm.get("beatmapset_id"):
                            s.beatmapset_id = bm["beatmapset_id"]
                        beatmapset = bm.get("beatmapset") or {}
                        if not s.creator and beatmapset.get("creator"):
                            s.creator = beatmapset["creator"]
            if scores:
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
        else:
            try:
                api_scores = await osu_api_client.get_user_best_scores(osu_user_id, limit=5)
            except Exception:
                api_scores = []
            base["top_scores"] = [
                {
                    "rank": s.get("rank", "F"),
                    "artist": (s.get("beatmapset") or {}).get("artist", ""),
                    "title": (s.get("beatmapset") or {}).get("title", ""),
                    "version": (s.get("beatmap") or {}).get("version", ""),
                    "pp": s.get("pp") or 0,
                    "accuracy": (s.get("accuracy") or 0) * 100,
                    "max_combo": s.get("max_combo") or 0,
                    "mods": ",".join(
                        m.get("acronym", "") if isinstance(m, dict) else str(m)
                        for m in (s.get("mods") or [])
                    ),
                    "beatmap_id": (s.get("beatmap") or {}).get("id", 0),
                    "beatmapset_id": (s.get("beatmapset") or {}).get("id", 0),
                    "creator": (s.get("beatmapset") or {}).get("creator", ""),
                }
                for s in (api_scores or [])
            ]

    return base


@router.message(TextTriggerFilter("profile", "pf"))
async def show_profile(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id

    if not osu_api_client:
        await message.answer("Ошибка: API-клиент не инициализирован.")
        return

    query = (trigger_args.args or "").strip() if trigger_args else ""
    async with get_db_session() as session:
        try:
            public_lookup = False
            tg_handle = None  # Telegram identity of the profile owner, when known
            # Precedence: explicit query > reply-to-user > sender. Replying to
            # someone with bare "pf" shows their profile (Telegram-native UX).
            if not query:
                reply_user = await get_reply_target_user(session, message, chat_id=tenant_chat_id)
                if reply_user and reply_user.osu_user_id:
                    user = reply_user
                    if message.reply_to_message:
                        tg_handle = _tg_handle(message.reply_to_message.from_user)
                else:
                    user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
                    if not user:
                        return
                    tg_handle = _tg_handle(message.from_user)
            else:
                user, user_data, status = await _resolve_profile_user(session, osu_api_client, tg_id, tenant_chat_id, query)
                if status == "not_found" or not user_data:
                    await message.answer(
                        f"Пользователь <b>{escape_html(query)}</b> не найден в osu!.",
                        parse_mode="HTML",
                    )
                    return
                if status == "unregistered":
                    user = user_data
                    public_lookup = True

            # Auto-update if stale only for self-profile
            if not public_lookup and getattr(user, "telegram_id", None) == tg_id:
                if needs_blocking_refresh(user.last_api_update):
                    wait_msg = await message.answer("Загрузка свежих данных из osu!...")
                    ok = await refresh_user(user, session, osu_api_client, mode="full")
                    if ok:
                        await session.commit()
                        await session.refresh(user)
                        await wait_msg.delete()
                    else:
                        await wait_msg.edit_text("Не удалось получить данные из osu! API. Показаны кешированные данные.")

            # Single dashboard card — no inline navigation.
            try:
                data = await _build_page_data(user, osu_api_client, session, tg_handle=tg_handle)
                buf = await card_renderer.generate_profile_dashboard_async(data)
                photo = BufferedInputFile(buf.read(), filename="profile.png")
                osu_id = data.get("osu_id")
                keyboard = None
                if osu_id:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="🔗 Профиль osu!",
                            url=f"https://osu.ppy.sh/users/{osu_id}",
                        )
                    ]])
                await message.answer_photo(photo=photo, reply_markup=keyboard)
            except Exception as img_err:
                logger.warning(f"Profile card generation failed: {img_err}", exc_info=True)
                await message.answer("Ошибка генерации карточки профиля.")

        except Exception as e:
            logger.error(f"Error in /profile for {tg_id}: {e}", exc_info=True)
            await message.answer("Произошла ошибка при загрузке профиля.")


@router.message(TextTriggerFilter("refresh"))
async def refresh_profile(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id

    if not osu_api_client:
        await message.answer("Ошибка: API-клиент не инициализирован.")
        return

    wait_msg = None
    async with get_db_session() as session:
        try:
            user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
            if not user:
                return

            wait_msg = await message.answer(
                "Загрузка данных из osu! API...\n\n<i>Это может занять несколько секунд</i>",
                parse_mode="HTML"
            )

            ok = await refresh_user(user, session, osu_api_client, mode="full")

            if ok:
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
