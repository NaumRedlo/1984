"""TOP PLAYS dashboard — `tpp`. Paged list of the player's best scores ranked
by weighted pp (osu!'s own rank-N-counts-0.95**(N-1) system), with pp-delta
badges ("+14pp 2 days ago" / "NEW").

Also reachable from `/pf` via an inline button (tpp|open), which swaps the
profile photo in place and offers a "back to profile" button (tpp|back) —
see bot/handlers/profile/handlers.py's keyboard.
"""

from datetime import timedelta
from typing import Dict, Optional

from aiogram import Router, types
from aiogram.types import (
    BufferedInputFile, InputMediaPhoto,
    InlineKeyboardButton, InlineKeyboardMarkup,
)

from sqlalchemy import select

from db.database import get_db_session
from db.models.best_score import UserBestScore
from services.image import card_renderer
from services.image.render.top_plays import build_top_plays_card_data
from utils.best_scores import build_top_plays_list
from utils.language import get_language
from utils.logger import get_logger
from utils.osu.resolve_user import get_reply_target_user, get_registered_user, resolve_osu_query_status
from utils.formatting.text import escape_html
from services.refresh import refresh_user, needs_blocking_refresh
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.common.auth import require_registered_user
from bot.utils.safe_edit import safe_edit_media
from utils.timeutils import utcnow

router = Router(name="top_plays")
logger = get_logger("handlers.top_plays")

# ── Per-viewer nav cache — mirrors bot/handlers/titles/handlers.py ──
_NAV_CACHE: Dict[int, dict] = {}
_TTL = timedelta(minutes=15)


def _store_nav(uid: int, payload: dict) -> None:
    payload["expires_at"] = utcnow() + _TTL
    _NAV_CACHE[uid] = payload


def _get_nav(uid: int) -> Optional[dict]:
    rec = _NAV_CACHE.get(uid)
    if not rec:
        return None
    if utcnow() > rec["expires_at"]:
        del _NAV_CACHE[uid]
        return None
    return rec


def _tg_handle(from_user) -> Optional[str]:
    username = getattr(from_user, "username", None) if from_user else None
    return f"@{username}" if username else None


def _tp_keyboard(uid: int, page: int, total_pages: int, *, show_back: bool) -> InlineKeyboardMarkup:
    rows = []
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="◀", callback_data=f"tpp|p|{uid}|{page - 1}")
            if page > 0 else InlineKeyboardButton(text="◀", callback_data="tpp|x"),
            InlineKeyboardButton(text=f"Стр. {page + 1}/{total_pages}", callback_data="tpp|x"),
            InlineKeyboardButton(text="▶", callback_data=f"tpp|p|{uid}|{page + 1}")
            if page < total_pages - 1 else InlineKeyboardButton(text="▶", callback_data="tpp|x"),
        ])
    if show_back:
        rows.append([InlineKeyboardButton(text="◀ Назад к профилю", callback_data=f"tpp|back|{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Data assembly ────────────────────────────────────────────────────────

async def _fetch_best_scores(session, user_id: int):
    stmt = select(UserBestScore).where(UserBestScore.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalars().all()


def _flatten_raw_score(raw: dict) -> dict:
    """Adapt a raw osu! API best-score payload into the flat shape
    build_top_plays_list expects. Only used for unregistered public lookups —
    there's no DB row, so no pp-delta history either (previous_pp always None,
    so these rows never show a NEW/changed badge — expected and correct)."""
    beatmapset = raw.get("beatmapset") or {}
    beatmap = raw.get("beatmap") or {}
    mods_list = raw.get("mods") or []
    mods_str = ",".join(m if isinstance(m, str) else m.get("acronym", "") for m in mods_list)
    acc = raw.get("accuracy")
    return {
        "score_id": raw.get("id"),
        "beatmap_id": beatmap.get("id", 0),
        "beatmapset_id": beatmapset.get("id"),
        "artist": beatmapset.get("artist", ""),
        "title": beatmapset.get("title", ""),
        "version": beatmap.get("version", ""),
        "creator": beatmapset.get("creator", ""),
        "mods": mods_str,
        "star_rating": beatmap.get("difficulty_rating") or 0.0,
        "accuracy": round(acc * 100, 2) if acc is not None else 0.0,
        "max_combo": raw.get("max_combo", 0),
        "rank": raw.get("rank", "F"),
        "pp": raw.get("pp") or 0.0,
        "previous_pp": None,
        "pp_changed_at": None,
    }


async def _build_payload(session, user, osu_api_client, tg_handle: Optional[str], *, public_lookup: bool = False) -> dict:
    if public_lookup:
        raw_scores = await osu_api_client.get_user_best_scores(user["id"], limit=100) if osu_api_client else []
        built = build_top_plays_list([_flatten_raw_score(r) for r in raw_scores])
        username = user.get("username", "???")
        country_field = user.get("country")
        country = country_field.get("code") if isinstance(country_field, dict) else country_field
        avatar_url = user.get("avatar_url")
        # user_data here is the already-flattened dict from
        # OsuApiClient.get_user_data (cover_url/global_rank/pp/accuracy are
        # top-level keys there, not nested cover{}/statistics{} — that
        # nesting only exists in the raw API response it was built from).
        cover_url = user.get("cover_url")
        global_rank = user.get("global_rank")
        player_pp = user.get("pp")
        accuracy = user.get("accuracy")
        card_lang = "en"
    else:
        scores = await _fetch_best_scores(session, user.id)
        built = build_top_plays_list(scores)
        username = user.osu_username
        country = user.country
        avatar_url = user.avatar_url
        cover_url = user.cover_url
        global_rank = user.global_rank
        player_pp = user.player_pp
        accuracy = user.accuracy
        card_lang = (await get_language(user.telegram_id)).lower()
    return {
        "built": built,
        "username": username,
        "handle": tg_handle,
        "country": country,
        "avatar_url": avatar_url,
        "cover_url": cover_url,
        "global_rank": global_rank,
        "player_pp": player_pp,
        "accuracy": accuracy,
        "lang": card_lang,
        "has_back": False,
    }


async def _render(message, uid: int, page: int, payload: dict, *, edit: bool) -> None:
    data = build_top_plays_card_data(
        payload["username"], payload["handle"], payload["country"],
        payload["built"], page=page, avatar_url=payload["avatar_url"], lang=payload["lang"],
        cover_url=payload.get("cover_url"), global_rank=payload.get("global_rank"),
        player_pp=payload.get("player_pp"), accuracy=payload.get("accuracy"),
    )
    payload["cur_page"] = data["page"]
    payload["cur_total_pages"] = data["total_pages"]
    buf = await card_renderer.generate_top_plays_card_async(data)
    kb = _tp_keyboard(uid, data["page"], data["total_pages"], show_back=payload.get("has_back", False))
    file = BufferedInputFile(buf.getvalue(), filename="top_plays.png")
    try:
        if edit:
            await safe_edit_media(message, media=InputMediaPhoto(media=file), reply_markup=kb)
        else:
            await message.answer_photo(photo=file, reply_markup=kb)
    except Exception as e:
        logger.debug(f"top plays render send failed: {e}")


# ── Command ────────────────────────────────────────────────────────────────

@router.message(TextTriggerFilter("tpp"))
async def show_top_plays(message: types.Message, osu_api_client=None, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id
    query = (trigger_args.args or "").strip() if trigger_args else ""
    async with get_db_session() as session:
        try:
            public_lookup = False
            tg_handle = None
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
                    if osu_api_client and getattr(user, "telegram_id", None) == tg_id \
                            and needs_blocking_refresh(user.last_api_update):
                        wait = await message.answer("Загрузка свежих данных из osu!...")
                        ok = await refresh_user(user, session, osu_api_client, mode="full")
                        if ok:
                            await session.commit()
                            await session.refresh(user)
                            await wait.delete()
                        else:
                            await wait.edit_text("Не удалось обновить, показаны кешированные данные.")
            else:
                if not osu_api_client:
                    await message.answer("Ошибка: API-клиент не инициализирован.")
                    return
                reg_user, user_data, status = await resolve_osu_query_status(session, osu_api_client, query, tenant_chat_id)
                if status == "not_found" or not user_data:
                    await message.answer(
                        f"Пользователь <b>{escape_html(query)}</b> не найден в osu!.",
                        parse_mode="HTML")
                    return
                if status == "unregistered":
                    user = user_data
                    public_lookup = True
                else:
                    user = reg_user

            payload = await _build_payload(session, user, osu_api_client, tg_handle, public_lookup=public_lookup)
            _store_nav(tg_id, payload)
            await _render(message, tg_id, 0, payload, edit=False)
        except Exception as e:
            logger.error(f"Error in /tpp for {tg_id}: {e}", exc_info=True)
            await message.answer("Произошла ошибка при загрузке топ-плеев.")


# ── Callbacks ──────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("tpp|p|"))
async def on_tpp_page(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _, uid_str, page_str = parts
    try:
        uid = int(uid_str)
        page = int(page_str)
    except ValueError:
        await callback.answer()
        return
    if callback.from_user.id != uid:
        await callback.answer("Не ваши топ-плеи.", show_alert=True)
        return
    payload = _get_nav(uid)
    if not payload:
        await callback.answer("Устарело — запустите tpp снова.", show_alert=True)
        return
    await callback.answer()
    await _render(callback.message, uid, page, payload, edit=True)


@router.callback_query(lambda c: c.data == "tpp|x")
async def on_tpp_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("tpp|open|"))
async def on_tpp_open(callback: types.CallbackQuery, tenant_chat_id=None) -> None:
    parts = callback.data.split("|", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    _, _, uid_str = parts
    try:
        uid = int(uid_str)
    except ValueError:
        await callback.answer()
        return
    if callback.from_user.id != uid:
        await callback.answer("Не ваш профиль.", show_alert=True)
        return
    await callback.answer()
    async with get_db_session() as session:
        user = await get_registered_user(session, uid, tenant_chat_id)
        if not user:
            await callback.answer("Профиль не найден.", show_alert=True)
            return
        payload = await _build_payload(session, user, None, _tg_handle(callback.from_user))
    payload["has_back"] = True
    _store_nav(uid, payload)
    await _render(callback.message, uid, 0, payload, edit=True)


@router.callback_query(lambda c: c.data and c.data.startswith("tpp|back|"))
async def on_tpp_back(callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None) -> None:
    parts = callback.data.split("|", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    _, _, uid_str = parts
    try:
        uid = int(uid_str)
    except ValueError:
        await callback.answer()
        return
    if callback.from_user.id != uid:
        await callback.answer("Не ваш профиль.", show_alert=True)
        return
    await callback.answer()
    # Deliberately no live API refresh here — /pf just did one moments ago;
    # re-reads the same (already fresh) DB row.
    from bot.handlers.profile.handlers import _build_page_data, _pf_keyboard
    async with get_db_session() as session:
        user = await get_registered_user(session, uid, tenant_chat_id)
        if not user:
            await callback.answer("Профиль не найден.", show_alert=True)
            return
        data = await _build_page_data(user, osu_api_client, session, tg_handle=_tg_handle(callback.from_user))
    buf = await card_renderer.generate_profile_dashboard_async(data)
    photo = BufferedInputFile(buf.read(), filename="profile.png")
    kb = _pf_keyboard(data.get("osu_id"), uid)
    try:
        await safe_edit_media(callback.message, media=InputMediaPhoto(media=photo), reply_markup=kb)
    except Exception as e:
        logger.debug(f"back-to-profile render send failed: {e}")


__all__ = ["router"]
