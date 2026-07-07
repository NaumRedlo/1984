from typing import Optional, Dict

from aiogram import Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.database import get_db_session
from db.models.best_score import UserBestScore
from services.image import card_renderer
from utils.logger import get_logger
from utils.osu.resolve_user import get_registered_user, get_reply_target_user, resolve_osu_query_status
from utils.formatting.text import escape_html
from utils.titles import TITLE_REGISTRY
from utils.title_progress import bump_profile_opens
from utils.i18n import t
from utils.language import get_language
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.common.auth import require_registered_user
from services.refresh import refresh_user, needs_blocking_refresh

router = Router(name="profile")
logger = get_logger("handlers.profile")


def _format_play_time(seconds: int, lang: str = "en") -> str:
    if not seconds or seconds <= 0:
        return "—"
    hours = seconds // 3600
    suffix = "ч" if (lang or "en").lower() == "ru" else "h"
    return f"{hours}{suffix}"


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


def _pf_keyboard(osu_id, subject_tg_id: Optional[int] = None, viewer_tg_id: Optional[int] = None,
                 lang: str = "en") -> Optional[InlineKeyboardMarkup]:
    """Shared with bot/handlers/profile/top_plays.py's "back to profile" nav —
    same two buttons every /pf render gets. `subject_tg_id` (the profile's
    owner, not the viewer) is only known for registered users; public
    unregistered lookups get just the osu! link.

    `viewer_tg_id` (whoever is looking at THIS /pf render right now) is
    encoded into the button separately from subject_tg_id — needed since
    2026-07-05's fix: the callback's OWN ownership check must match whoever
    clicks it (the viewer), while the data to fetch is the subject's. Using
    one id for both silently broke the button for every cross-profile
    lookup (viewer clicking it got "not your profile" since they're never
    equal to the subject unless viewing their own profile)."""
    rows = []
    if osu_id:
        rows.append([InlineKeyboardButton(text=t("pf.kb.osu_profile", lang), url=f"https://osu.ppy.sh/users/{osu_id}")])
    if subject_tg_id and viewer_tg_id:
        rows.append([InlineKeyboardButton(text=t("pf.kb.top_plays", lang),
                                          callback_data=f"tpp|open|{viewer_tg_id}|{subject_tg_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _build_page_data(
    user, osu_api_client, session, tg_handle: Optional[str] = None,
    viewer_tg_id: Optional[int] = None,
) -> Dict:
    """Build the full data dict for the profile dashboard card.

    `tg_handle` is the ready-to-show Telegram identity of the profile's owner
    (``@username`` when public, else the display name) from the message context;
    it's shown under the name instead of the osu! handle. None falls back to the
    osu! username in the renderer.

    `viewer_tg_id` is whoever is LOOKING at this card right now — the card
    renders in THEIR language preference, not the profile subject's (fixed
    2026-07-05; previously showed the subject's own language even when a
    different person requested it, e.g. a `/pf <nickname>` lookup showing in
    the looked-up player's language instead of the requester's).
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
            }
            for key in aliases.get(field, [field]):
                if key in user:
                    return user.get(key, default)
            return default
        return getattr(user, field, default)

    # Card text follows the VIEWER's language, not the profile subject's (see
    # viewer_tg_id's docstring note above) — falls back to the subject's own
    # language only when no viewer is known at all (shouldn't normally
    # happen; kept as a safety net rather than a hard requirement).
    fallback_tg_id = _get("telegram_id", None)
    lang_tg_id = viewer_tg_id if viewer_tg_id is not None else fallback_tg_id
    card_lang = await get_language(lang_tg_id) if lang_tg_id else "EN"

    base = {
        "username": _get("osu_username", "???"),
        "handle": tg_handle or None,
        "osu_id": _get("osu_user_id", 0),
        "pp": _get("player_pp", 0) or 0,
        "global_rank": _get("global_rank", 0) or 0,
        "country": _get("country", "—") or "—",
        "accuracy": _get("accuracy", 0.0) or 0.0,
        "play_count": _get("play_count", 0) or 0,
        "play_time": _format_play_time(_get("play_time", 0) or 0, card_lang),
        "ranked_score": _get("ranked_score", 0) or 0,
        "total_hits": _get("total_hits", 0) or 0,
        "total_score": _get("total_score", 0) or 0,
        "avatar_url": _get("avatar_url", None),
        "cover_url": _get("cover_url", None),
        "lang": card_lang,
    }

    # Active title chip — registered users only; falls back to nothing.
    base["title"] = None
    base["title_color"] = None
    if not isinstance(user, dict):
        tc = getattr(user, "active_title_code", None)
        if tc:
            td = TITLE_REGISTRY.get(tc)
            if td:
                base["title"] = td.name_for(card_lang.lower())
                base["title_color"] = td.color

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


@router.message(TextTriggerFilter("pf"))
async def show_profile(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id
    lang = (await get_language(tg_id)).lower()

    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
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
                        t("pf.user_not_found", lang, name=escape_html(query)),
                        parse_mode="HTML",
                    )
                    return
                if status == "unregistered":
                    user = user_data
                    public_lookup = True

            # Count own-profile opens toward "Still Here" (5 in a UTC day).
            is_self = not public_lookup and getattr(user, "telegram_id", None) == tg_id
            if is_self:
                bump_profile_opens(user)
                await session.commit()

            # Auto-update if stale only for self-profile
            if is_self:
                if needs_blocking_refresh(user.last_api_update):
                    wait_msg = await message.answer(t("pf.refreshing", lang))
                    ok = await refresh_user(user, session, osu_api_client, mode="full")
                    if ok:
                        await session.commit()
                        await session.refresh(user)
                        await wait_msg.delete()
                    else:
                        await wait_msg.edit_text(t("pf.refresh_failed_cached", lang))

            # Single dashboard card + a link out and (registered subjects only)
            # a button into the full top-plays card.
            try:
                data = await _build_page_data(user, osu_api_client, session, tg_handle=tg_handle, viewer_tg_id=tg_id)
                buf = await card_renderer.generate_profile_dashboard_async(data)
                photo = BufferedInputFile(buf.read(), filename="profile.png")
                subject_tg_id = getattr(user, "telegram_id", None) if not isinstance(user, dict) else None
                keyboard = _pf_keyboard(data.get("osu_id"), subject_tg_id, tg_id, lang)
                await message.answer_photo(photo=photo, reply_markup=keyboard)
            except Exception as img_err:
                logger.warning(f"Profile card generation failed: {img_err}", exc_info=True)
                await message.answer(t("pf.card_gen_failed", lang))

        except Exception as e:
            logger.error(f"Error in /profile for {tg_id}: {e}", exc_info=True)
            await message.answer(t("pf.load_error", lang))


@router.message(TextTriggerFilter("rf"))
async def refresh_profile(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id
    lang = (await get_language(tg_id)).lower()

    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
        return

    wait_msg = None
    async with get_db_session() as session:
        try:
            user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
            if not user:
                return

            wait_msg = await message.answer(t("rf.loading", lang), parse_mode="HTML")

            ok = await refresh_user(user, session, osu_api_client, mode="full")

            if ok:
                await session.commit()
                await session.refresh(user)
                await wait_msg.edit_text(t("rf.success", lang), parse_mode="HTML")
            else:
                await wait_msg.edit_text(t("rf.failed", lang), parse_mode="HTML")

        except Exception as e:
            logger.error(f"Unhandled exception in /refresh for {tg_id}: {e}", exc_info=True)
            error_text = t("rf.error", lang)
            if wait_msg:
                await wait_msg.edit_text(error_text)
            else:
                await message.answer(error_text)

__all__ = ["router"]
